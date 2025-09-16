import concurrent.futures
from copy import deepcopy
from datetime import datetime

import requests
from dateutil import tz

from datasphere.automation import DatasphereAutomation
from datasphere.custom_types import (
    StatisticsDict,
    StatisticsInformationDict,
    StatisticsType,
)
from utils.filehandler import settings
from utils.logging import logger

# Wichtige Bedingungen aus Settings
URL_TO_USE: str = settings["Setup"]["URL_TO_USE"]

# Wichtige URLs aus Settings
DATASPHERE_URL: str = settings["URLs"][URL_TO_USE]


class RemoteTables(DatasphereAutomation):
    def __init__(self, session: requests.Session | None = None):
        # DatasphereAutomation initialisieren
        super().__init__(session)

    def _get_all_table_names(self) -> StatisticsDict:
        """
        Gibt alle Tabellennamen als formatiertes Dictionary zurück.

        Returns:
            dict: Dictionary mit Tabellennamen als Schlüssel und einem weiteren
                  Dictionary mit Informationen als Wert.
        """

        # Alle Tabellennamen auslesen
        response = self.session.get(
            url=f"{DATASPHERE_URL}/dwaas-core/statistics/BWBRIDGESPACE"
            f"/remotetables?includeBusinessNames=true",
            json={"includeBusinessNames": True},
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

    def create_statistics(self, type: StatisticsType = "HISTOGRAM") -> None:
        """
        Erstellt Statistiken für alle Tabellen.

        Args:
            type (StatisticsType): Typ der Statistik. Standard ist 'HISTOGRAM'.
        """

        # Alle Tabellennamen lesen
        all_tables = self._get_all_table_names()

        # Über alle Tabellennamen iterieren und Statistik erstellen
        for table in all_tables:
            # Nur Statistiken anlegen bei Tabellen, die sie unterstützen
            if (
                all_tables[table]["statisticsSupported"]
                and all_tables[table]["statisticsType"] != type
            ):
                if all_tables[table]["statisticsType"] is None:
                    response = self.session.post(
                        url=f"{DATASPHERE_URL}/dwaas-core/statistics"
                        f"/BWBRIDGESPACE/remoteTables/{table}?type={type}",
                        json={"type": type},
                    )
                elif all_tables[table]["statisticsType"] != type:
                    response = self.session.put(
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

    def refresh_statistics(
        self, use_threads: bool = True, thread_count: int = 5
    ) -> None:
        """
        Aktualisiert Statistiken für alle Tabellen in der
        Datei 'table_names.txt'.
        """

        # Alle Tabellennamen lesen
        all_tables = self._get_all_table_names()

        # Funktion, um Statistiken zu aktualisieren
        # Nur Statistiken anlegen bei Tabellen, die sie unterstützen
        # und eine Statistik haben
        def refresh_statistics_for_table(
            session: requests.Session, table: str
        ) -> None:
            if (
                all_tables[table]["statisticsSupported"]
                and all_tables[table]["statisticsType"] is not None
            ):
                response = session.post(
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

        # Falls Threads genutzt werden sollen
        if use_threads:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=thread_count
            ) as executor:
                for table in all_tables:
                    executor.submit(
                        refresh_statistics_for_table,
                        deepcopy(self.session),
                        table,
                    )

        # Falls keine Threads genutzt werden sollen
        else:
            # Über alle Tabellennamen iterieren und Statistik aktualisieren
            for table in all_tables:
                refresh_statistics_for_table(self.session, table)
