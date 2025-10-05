from datetime import datetime

import httpx
from dateutil import tz

from datasphere.automation import DatasphereAutomation
from utils.filehandler import settings
from utils.logging import logger
from utils.types import (
    StatisticsDict,
    StatisticsInformationDict,
    StatisticsType,
)

# Wichtige Bedingungen aus Settings
URL_TO_USE: str = settings["Setup"]["URL_TO_USE"]

# Wichtige URLs aus Settings
DATASPHERE_URL: str = settings["URLs"][URL_TO_USE]


class RemoteTables(DatasphereAutomation):
    def __init__(self, session: httpx.AsyncClient | None = None):
        if session is not None:
            self.session = session
        else:
            super().__init__()

    async def initialize(self) -> None:
        """
        Initialisiert die Datasphere Session.
        """
        self.session: httpx.AsyncClient = await (
            self.initialize_datasphere_session()
        )

    async def _get_all_table_names(self) -> StatisticsDict:
        """
        Gibt alle Tabellennamen als formatiertes Dictionary zurück.

        Returns:
            dict: Dictionary mit Tabellennamen als Schlüssel und einem weiteren
                  Dictionary mit Informationen als Wert.
        """

        # Alle Tabellennamen auslesen
        logger.debug("Lade alle Remote Tables...")
        response = await self.session.get(
            url=(
                f"{DATASPHERE_URL}/dwaas-core/statistics/BWBRIDGESPACE"
                f"/remotetables?includeBusinessNames=true"
            ),
        )
        all_tables: StatisticsDict = {}
        for table in response.json()["tables"]:
            statistics_information: StatisticsInformationDict = {
                "statisticsSupported": table.get("statisticsSupported", True),
                "statisticsLimitedToRecordCount": table.get(
                    "statisticsLimitedToRecordCount", False
                ),
                "statisticsType": table.get("statisticsType"),
                "businessName": table.get("businessName", ""),
                "statisticsLatestUpdate": table.get("statisticsLatestUpdate"),
            }
            all_tables[table["tableName"]] = statistics_information

        # Alle Werte bei "statisticsLatestUpdate" in Datetime-Objekt mit
        # korrekter Zeitzone umwandeln
        for table in all_tables.values():
            if isinstance(table["statisticsLatestUpdate"], str):
                converted_dt = datetime.strptime(
                    table["statisticsLatestUpdate"],
                    "%Y-%m-%d %H:%M:%S.%f000000",
                )
                converted_dt = converted_dt.replace(tzinfo=tz.gettz("UTC"))
                converted_dt_with_timezone = converted_dt.astimezone(
                    tz.gettz("Europe/Berlin")
                )
                table["statisticsLatestUpdate"] = converted_dt_with_timezone

        return all_tables

    async def create_statistics(
        self, type: StatisticsType = "HISTOGRAM", thread_count: int = 5
    ) -> None:
        """
        Erstellt Statistiken für alle Tabellen.

        Args:
            type (StatisticsType): Typ der Statistik. Standard ist 'HISTOGRAM'.
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                          Anfragen. Standard ist 5.
        """

        # Alle Tabellennamen lesen
        all_tables = await self._get_all_table_names()

        # Funktion, um Statistiken zu erstellen
        async def create_statistics_for_table(table: str) -> None:
            # Nur Statistiken anlegen bei Tabellen, die sie unterstützen
            if (
                all_tables[table]["statisticsSupported"]
                and all_tables[table]["statisticsType"] != type
            ):
                if all_tables[table]["statisticsType"] is None:
                    response = await self.session.post(
                        url=f"{DATASPHERE_URL}/dwaas-core/statistics"
                        f"/BWBRIDGESPACE/remoteTables/{table}?type={type}",
                        json={"type": type},
                    )
                elif all_tables[table]["statisticsType"] != type:
                    response = await self.session.put(
                        url=f"{DATASPHERE_URL}/dwaas-core/statistics"
                        f"/BWBRIDGESPACE/remoteTables/{table}?type={type}",
                        json={"type": type},
                    )

                # Antwort auswerten
                if (
                    response.status_code == 500
                    and "STATISTICS_ALREADY_EXISTS" in response.text
                ):
                    logger.debug(
                        "Statistik für Tabelle %s bereits vorhanden. "
                        "Wird übersprungen...",
                        table,
                    )
                elif response.status_code == 202:
                    logger.info("Statistik für Tabelle %s erstellt.", table)
                else:
                    logger.error(
                        "Fehler beim Erstellen der Statistik für Tabelle %s. "
                        "Status Code: %s",
                        table,
                        response.status_code,
                    )
                    logger.debug("Response: %s\n", response.text)

        # Über alle Tabellennamen iterieren und Statistik erstellen
        await self.run_async_tasks(
            all_tables,
            create_statistics_for_table,
            thread_count
        )

    async def refresh_statistics(self, thread_count: int = 5) -> None:
        """
        Aktualisiert Statistiken für alle Tabellen in der
        Datei 'table_names.txt'.

        Args:
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                          Anfragen. Standard ist 5.
        """

        # Alle Tabellennamen lesen
        all_tables = await self._get_all_table_names()

        # Funktion, um Statistiken zu aktualisieren
        # Nur Statistiken anlegen bei Tabellen, die sie unterstützen
        # und eine Statistik haben
        async def refresh_statistics_for_table(table: str) -> None:
            if (
                all_tables[table]["statisticsSupported"]
                and all_tables[table]["statisticsType"] is not None
            ):
                response = await self.session.post(
                    url=f"{DATASPHERE_URL}/dwaas-core/statistics/"
                    f"BWBRIDGESPACE/remoteTables/{table}/refresh"
                )
                if response.status_code == 202:
                    logger.info(
                        "Statistik für Tabelle %s aktualisiert.", table
                    )
                else:
                    logger.error(
                        "Fehler beim Aktualisieren der Statistik für %s. "
                        "Status Code: %s",
                        table,
                        response.status_code,
                    )
                    logger.debug("Response: %s\n", response.text)

        # Über alle Tabellennamen iterieren und Statistik aktualisieren
        await self.run_async_tasks(
            all_tables,
            refresh_statistics_for_table,
            thread_count
        )
