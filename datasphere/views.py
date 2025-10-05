import asyncio
import contextlib
import csv
from json.decoder import JSONDecodeError
from urllib.parse import quote, urlencode
from uuid import uuid4

import httpx
import pandas as pd

from datasphere.automation import DatasphereAutomation
from utils.filehandler import ALL_FILES, settings
from utils.logging import logger
from utils.types import ViewDetailsDict

# Wichtige Bedingungen aus Settings
URL_TO_USE: str = settings["Setup"]["URL_TO_USE"]

# Wichtige URLs aus Settings
DATASPHERE_URL: str = settings["URLs"][URL_TO_USE]


class Views(DatasphereAutomation):
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

    async def _get_all_views(self) -> list[ViewDetailsDict]:
        """
        Gibt alle Views als Liste von Dictionaries zurück.

        Returns:
            list[ViewDetailsDict]: Liste von Dictionaries mit
                                   View-Namen ("name") und detaillierten
                                   Informationen.
        """
        # Headers anpassen
        for header in ("X-Csrf-Token", "X-Requested-With", "Priority"):
            with contextlib.suppress(KeyError):
                self.session.headers.pop(header)
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Accept-Language": "de",
                "UI5-Timezone": "Europe/Berlin",
                "UI5-Timepattern": "H%3Amm%3Ass",
                "UI5-Datepattern": "dd.MM.yyyy",
                "Cache-Control": "no-cache",
            }
        )

        # Abfrage vorbereiten
        url = f"{DATASPHERE_URL}/deepsea/repository/search/$all"
        params = {
            "$top": 10000,  # kann nicht weggelassen werden
            "$skip": 0,
            "whyfound": "true",
            "$count": "true",
            "valuehierarchy": "folder_id",
            "facets": "all",
            "facetlimit": 5,
            "$apply": (
                "filter(Search.search(query='SCOPE:SEARCH_DESIGN "
                '(technical_type_description:EQ(S):"View" AND (technical_type:'
                'EQ(S):"DWC_REMOTE_TABLE" OR technical_type:EQ(S):'
                '"DWC_LOCAL_TABLE" OR technical_type:EQ(S):"DWC_VIEW" OR '
                'technical_type:EQ(S):"DWC_ERMODEL" OR technical_type:EQ(S):'
                '"DWC_DATAFLOW" OR technical_type:EQ(S):"DWC_IDT" OR '
                'technical_type:EQ(S):"DWC_BUSINESS_ENTITY" OR technical_type:'
                'EQ(S):"DWC_AUTH_SCENARIO" OR technical_type:EQ(S):'
                '"DWC_FACT_MODEL" OR technical_type:EQ(S):'
                '"DWC_CONSUMPTION_MODEL" OR technical_type:EQ(S):'
                '"DWC_PERSPECTIVE" OR kind:EQ(S):"sap.dis.dataflow" OR kind:'
                'EQ(S):"sap.dwc.dac" OR kind:EQ(S):"sap.repo.folder" OR kind:'
                'EQ(S):"sap.dwc.analyticModel" OR kind:EQ(S):'
                '"sap.dwc.taskChain" OR kind:EQ(S):"sap.dis.replicationflow" '
                'OR technical_type:EQ(S):"DWC_TRANSFORMATIONFLOW")) *\'))'
            ),
        }

        # Anfrage senden
        logger.debug("Lade alle Views...")
        response = await self.session.get(
            url=f"{url}?{urlencode(params, safe='()*', quote_via=quote)}"
        )
        all_views: list[ViewDetailsDict] = response.json()["value"]

        # Nicht benötigte Headers für weitere Requests wieder entfernen
        for header in (
            "Origin",
            "UI5-Timezone",
            "UI5-Timepattern",
            "UI5-Datepattern",
            "Cache-Control",
        ):
            with contextlib.suppress(KeyError):
                self.session.headers.pop(header)

        return all_views

    async def get_all_views_where_attribute_contains(self, word: str,
    thread_count: int = 1) -> None:
        """
        Gibt alle Views als CSV-Datei aus, die ein Attribut haben,
        dass das Suchwort enthält.

        Args:
            word (str): Suchwort (case-insensitive).
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                          Anfragen. Standard ist 1.
        """
        # Alle Views abfragen
        all_views = await self._get_all_views()

        # Headers anpassen
        # (Voraussetzung: vorher wird immer get_all_view_names() aufgerufen)
        self.session.headers.update(
            {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            }
        )

        # Abfrage vorbereiten
        logger.debug(
            "Suche nach Views, die ein Attribut haben, "
            "dass den Substring '%s' enthält...",
            word,
        )

        # Funktion, um zu prüfen ob View ein passendes Attribut hat
        async def check_view_for_attribute_with_substring(view) -> None:

            # Parameter anpassen
            params = {
                "ids": view["id"],
                "details": (
                    "id,#repairedCsn,#ownerBusinessName,#creatorBusinessName,"
                    "#repositoryPackage,@EnterpriseSearch.enabled,@remote.source,"
                    "@DataWarehouse.external.schema,#objectPathIdentifier,"
                    "#repositoryPackage,#repositoryValidationDate,hasPendingError,"
                    "#isI18nEnabled"
                ),
                "kinds": (
                    "entity,view,sap.dwc.ermodel,sap.dis.dataflow,"
                    "sap.dwc.taskChain,sap.dwc.analyticModel,"
                    "sap.dwc.dac,sap.repo.folder,sap.dis.replicationflow,"
                    "sap.dis.transformationflow,sap.dwc.perspective,"
                    "sap.dwc.consumptionModel,sap.dwc.factModel,"
                    "sap.dwc.businessEntity,sap.dwc.authscenario"
                ),
            }

            # Request-ID aktualisieren
            self.session.headers.update(
                {
                    "x-request-id": str(uuid4()).replace("-", ""),
                }
            )

            # Abfrage senden
            logger.debug(
                "Prüfe View %s in %s...", view["name"], view["space_name"]
            )
            response = await self.session.get(
                url=f"{DATASPHERE_URL}/deepsea/repository"
                f"/{view['space_name']}/designObjects",
                params=params,
            )
            try:
                view_data = response.json()
            except (httpx.HTTPError, JSONDecodeError):
                logger.error(
                    "Fehler beim Abfragen der View %s in %s.",
                    view["name"],
                    view["space_name"],
                )
                logger.debug(
                    "View: %s\nResponse: %s\n", view, response.text.strip()
                )
                return

            # Infos in Datei schreiben,
            # falls Attribut mit Suchwort enthalten ist
            for attribute in view_data["results"][0]["#repairedCsn"][
                "definitions"
            ][view["name"]]["elements"]:
                if word.lower() in attribute.lower():
                    logger.info(
                        "View %s in %s hat Attribut '%s'.",
                        view["name"],
                        view["space_name"],
                        attribute,
                    )
                    with open(
                        ALL_FILES["VIEW_ATTRIBUTE"]["absolute_path"],
                        "a",
                        newline="",
                        encoding="utf-8",
                    ) as file:
                        writer = csv.DictWriter(
                            file,
                            fieldnames=ALL_FILES["VIEW_ATTRIBUTE"]["columns"],
                        )
                        values = {
                            "entity": view["name"],
                            "space": view["space_name"],
                            "businessName": view["business_name"],
                            "attribute": attribute,
                        }
                        writer.writerow(values)

        # Tasks starten
        await self.run_async_tasks(
            all_views,
            check_view_for_attribute_with_substring,
            thread_count
        )

        # Finales Logging mit Dateipfad
        file_name = ALL_FILES["VIEW_ATTRIBUTE"]["absolute_path"]
        logger.info("Ergebnisse gespeichert in '%s'.", file_name)

    async def create_view_analytics(self, thread_count: int = 1) -> None:
        """
        Erstellt View-Analysen für alle Views. Threads können in geringer
        Anzahl genutzt werden, da es sonst zu Ratelimits kommen kann.
        Fünf Threads sind fehlerfrei durchgelaufen.

        Args:
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                          Anfragen. Standard ist 1.
        """

        # Alle Views abfragen
        all_views = await self._get_all_views()

        # Headers anpassen
        # (Voraussetzung: vorher wird immer get_all_view_names() aufgerufen)
        self.session.headers.update(
            {
                "x-request-id": str(uuid4()).replace("-", ""),
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
            }
        )

        async def create_view_analytics(
            view: ViewDetailsDict,
            filter_out_own_view: bool = False,
        ) -> None:
            """
            Beeinhaltet die Logik zur Erstellung der View-Analysen. 
            Schreibt alle Views, die in der Analyse mit einem Persistenz-Score
            von 10 bewertet wurden in eine Datei.

            Args:
                view (ViewDetailsDict): View, für die Analyse erstellt wird.
                filter_out_own_view (bool, optional): Wenn True, wird die 
                                                      eigene View aus der
                                                      Analyse ausgeschlossen.
                                                      Standard ist False.
            """

            # Abfrage vorbereiten
            logger.debug(
                "Starte View Analyse für %s in %s...",
                view["name"],
                view["space_name"],
            )
            space_name = view["space_name"]
            view_name = view["name"]
            url = (
                f"{DATASPHERE_URL}/dwaas-core/advisor/{space_name}"
                f"/execute/{view_name}"
            )
            data = {
                "withMemoryAnalysis": False,
                "maximumMemoryConsumptionInGiB": 1,
            }
            response = await self.session.post(url=url, json=data)

            # Auf Fehler prüfen
            if not (
                response.status_code == 409
                and "taskAlreadyRunning" in response.text
            ) and not (
                response.status_code == 202 
                and "Running" in response.text
            ):
                logger.error(
                    "Fehler beim Starten der View Analyse für %s in %s.",
                    view_name,
                    space_name,
                )
                return
            logger.info(
                "View Analyse für %s in %s gestartet.", view_name, space_name
            )

            # Request-ID aktualisieren
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )

            # Logs der letzten Läufe fetchen
            async def fetch_logs() -> list[dict]:
                response = await self.session.get(
                    url=f"{DATASPHERE_URL}/dwaas-core/tf/{space_name}/logs",
                    params={"objectId": view_name, "getLocks": True},
                )
                return response.json()["logs"]

            # Ergebnisse abwarten
            latest_status = None
            while latest_status != "COMPLETED":
                logs = await fetch_logs()
                latest_status = logs[0]["status"]
                if latest_status == "FAILED":
                    logger.error(
                        "Fehler beim Generieren der View Analyse "
                        "für %s in %s.",
                        view_name,
                        space_name,
                    )
                    return
                # TODO: hier noch aktuelle Laufzeit mit loggen, gibt nur
                # 'startTime': '2025-07-15T07:25:18.803Z' und 'runTime': 239
                # (in Sekunden)
                logger.debug(
                    "Warte auf Ergebnisse für %s in %s...",
                    view_name,
                    space_name
                )
                await asyncio.sleep(1)

            # Log-ID des letzten Laufs auslesen
            log_id: int = (await fetch_logs())[0]["logId"]

            # Request-ID aktualisieren
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )

            # Ergebnisse auslesen
            response = await self.session.get(
                url=(
                    f"{DATASPHERE_URL}/dwaas-core/advisor"
                    f"/{space_name}/result/{log_id}"
                )
            )

            # View mit besten Persistenz-Score ermitteln
            # (10 wird nur einmal vergeben)
            # Eigene View rausfiltern, wenn gewünscht, weil sonst kleinere 
            # Views immer selber Score 10 erhalten
            entity_stats = response.json()["entityStats"]
            if filter_out_own_view:
                entity_stats = list(
                    filter(
                        lambda entity: entity["entity"] != view_name,
                        entity_stats
                    )
                )
            best_view = list(
                filter(
                    lambda entity: entity.get("persistencyCandidateScore", 0)
                    == 10,
                    entity_stats,
                )
            )

            # Falls View mit Score 10 gefunden, in Datei schreiben
            if best_view:
                logger.info(
                    "View %s in %s hat Persistenz-Score 10.",
                    best_view[0]["entity"],
                    best_view[0]["space"],
                )
                with open(
                    ALL_FILES["VIEW_ANALYSE"]["absolute_path"],
                    "a",
                    newline="",
                ) as file:
                    writer = csv.DictWriter(
                        file,
                        fieldnames=ALL_FILES["VIEW_ANALYSE"]["columns"],
                    )
                    writer.writerow(
                        {
                            key: best_view[0][key]
                            for key in ALL_FILES["VIEW_ANALYSE"]["columns"]
                        }
                    )
            else:
                logger.debug("Keine View mit Persistenz-Score 10 gefunden.")

        # Tasks starten
        await self.run_async_tasks(
            all_views,
            create_view_analytics,
            thread_count
        )

        # Finales Logging mit Dateipfad
        file_name = ALL_FILES["VIEW_ANALYSE"]["absolute_path"]
        logger.info("Ergebnisse gespeichert in '%s'.", file_name)

    async def create_partitioning_for_views(
        self,
        partitions: list[str],
        overwrite_existing_partitions: bool = False,
        thread_count: int = 1,
    ) -> None:
        """
        Erstellt Partitionen für alle Views,
        die in der Datei 'views_to_partition.csv' enthalten sind.
        Benötigt die Task-Datei VIEW_PARTITIONING_FILE_PATH.
        Schreibt Ergebnisse in VIEW_PARTITIONING_RESULT_FILE_PATH.

        Args:
            partitions (list[str]): Liste aller Partitionen, die erstellt
                                    werden sollen, in richtiger Reihenfolge.
                                    Bsp.: ['0000', '2001', '2002', ...]
                                    Letzter Wert ist Obergrenze der letzten
                                    Partition (Bsp.: FISCYEAR < 2025).
                                    Muss deshalb mindestens zwei Werte haben.
            overwrite_existing_partitions (bool, optional): Wenn True, werden
                                                            bereits
                                                            existierende
                                                            Partitionen
                                                            überschrieben.
                                                            Andernfalls bleiben
                                                            sie bestehen.
                                                            Standard ist False.
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                          Anfragen. Standard ist 1.
        """

        # Task-Datei lesen
        views_to_partition = []
        with open(
            ALL_FILES["VIEW_PARTITIONING_CREATE"]["absolute_path"],
            newline="",
            encoding="utf-8",
        ) as file:
            reader = csv.DictReader(
                file,
                fieldnames=ALL_FILES["VIEW_PARTITIONING_CREATE"]["columns"],
            )
            views_to_partition = list(reader)[1:]

        # Headers anpassen
        # (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Origin")
        self.session.headers.pop("Priority")
        self.session.headers.update({"Accept": "*/*"})

        # Funktion, um zu prüfen, ob Partition bereits existiert
        async def create_partitioning_for_view(view) -> None:
            # Prüfen, ob Partition bereits existiert
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )
            response = await self.session.get(
                url=f"{DATASPHERE_URL}/dwaas-core/partitioning"
                f"/{view['space']}/persistedViews/{view['entity']}"
            )
            partition_exists = len(response.json()["ranges"]) > 0
            format_check = (
                response.json()["partitioningColumns"][view["attribute"]][
                    "type"
                ]
                == "cds.String"
            )

            # Prüfen, ob Partitionsspalte ein String ist
            if not format_check:
                logger.error(
                    "Attribut '%s' der View %s in %s ist kein String. "
                    "Wird übersprungen...",
                    view["attribute"],
                    view["entity"],
                    view["space"],
                )
                with open(
                    ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"][
                        "absolute_path"
                    ],
                    "a",
                    newline="",
                    encoding="utf-8",
                ) as file:
                    writer = csv.DictWriter(
                        file,
                        fieldnames=ALL_FILES[
                            "VIEW_PARTITIONING_CREATE_RESULT"
                        ]["columns"],
                    )
                    values = {
                        "entity": view["entity"],
                        "space": view["space"],
                        "attribute": view["attribute"],
                        "createdPartition": False,
                    }
                    writer.writerow(values)
                return

            # In Datei vermerken und überspringen, falls Partition bereits
            # existiert und nicht überschrieben werden soll
            if partition_exists and not overwrite_existing_partitions:
                logger.debug(
                    "%s in %s ist bereits partitioniert. Wird übersprungen...",
                    view["entity"],
                    view["space"],
                )
                with open(
                    ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"][
                        "absolute_path"
                    ],
                    "a",
                    newline="",
                    encoding="utf-8",
                ) as file:
                    writer = csv.DictWriter(
                        file,
                        fieldnames=ALL_FILES[
                            "VIEW_PARTITIONING_CREATE_RESULT"
                        ]["columns"],
                    )
                    values = {
                        "entity": view["entity"],
                        "space": view["space"],
                        "attribute": view["attribute"],
                        "createdPartition": True,
                    }
                    writer.writerow(values)
                return

            # Partitionen erstellen
            logger.debug(
                "Erstelle Partitionen für %s in %s...",
                view["entity"],
                view["space"],
            )
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )
            url = (
                f"{DATASPHERE_URL}/dwaas-core/partitioning/{view['space']}"
                f"/persistedViews/{view['entity']}"
            )
            data = {
                "remoteSourceName": "",
                "objectName": view["entity"],
                "numParallelPartitions": 1,
                "ranges": [
                    {
                        "id": index + 1,
                        "low": {"include": True, "value": partitions[index]},
                        "high": {
                            "include": False,
                            "value": partitions[index + 1],
                        },
                        "locked": False,
                    }
                    for index in range(len(partitions) - 1)
                ],
                "column": view["attribute"],
                "columnType": "cds.String",
                "runtimeDataCalculation": "designtime",
                "type": "range",
            }
            response = await self.session.post(url=url, json=data)

            # In Datei vermerken
            if response.status_code == 201:
                logger.info(
                    "Partitionen für %s in %s erstellt.",
                    view["entity"],
                    view["space"],
                )
            else:
                logger.error(
                    "Fehler beim Erstellen der Partitionen für %s in %s.",
                    view["entity"],
                    view["space"],
                )
                logger.debug("Response: %s\n", response.text)
            with open(
                ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"]["absolute_path"],
                "a",
                newline="",
            ) as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"][
                        "columns"
                    ],
                )
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "attribute": view["attribute"],
                    "createdPartition": response.status_code == 201,
                }
                writer.writerow(values)

        # Tasks starten
        await self.run_async_tasks(
            views_to_partition,
            create_partitioning_for_view,
            thread_count
        )

        # Finales Logging mit Dateipfad
        file_name = ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"][
            "absolute_path"
        ]
        logger.info("Ergebnisse gespeichert in '%s'.", file_name)

    async def remove_partitioning_for_views(self,
    thread_count: int = 1) -> None:
        """
        Entfernt Partitionen für alle Views,
        die in der Datei 'views_to_delete_partition.csv' enthalten sind.
        Benötigt die Task-Datei VIEW_TO_DELETE_PARTITIONING_FILE_PATH.
        Schreibt Ergebnisse in VIEW_TO_DELETE_PARTITIONING_RESULT_FILE_PATH.

        Args:
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                          Anfragen. Standard ist 1.
        """

        # Task-Datei lesen
        views_to_delete_partition = []
        with open(
            ALL_FILES["VIEW_PARTITIONING_DELETE"]["absolute_path"],
            newline="",
            encoding="utf-8",
        ) as file:
            reader = csv.DictReader(
                file,
                fieldnames=ALL_FILES["VIEW_PARTITIONING_DELETE"]["columns"],
            )
            views_to_delete_partition = list(reader)[1:]

        # Headers anpassen
        # (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Origin")
        self.session.headers.pop("Priority")
        self.session.headers.update({"Accept": "*/*"})

        # Funktion, um Partition zu entfernen
        async def remove_partitioning_for_view(view) -> None:
            # Partition entfernen
            logger.debug(
                "Entferne Partition für %s in %s...",
                view["entity"],
                view["space"],
            )
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )
            response = await self.session.delete(
                url=f"{DATASPHERE_URL}/dwaas-core/partitioning"
                f"/{view['space']}/persistedViews/{view['entity']}"
            )

            # Fehler prüfen
            if response.status_code != 200:
                logger.error(
                    "Fehler beim Entfernen der Partition für %s in %s.",
                    view["entity"],
                    view["space"],
                )
                return

            # In Datei vermerken
            logger.info(
                "Partition für %s in %s entfernt.",
                view["entity"],
                view["space"],
            )
            with open(
                ALL_FILES["VIEW_PARTITIONING_DELETE_RESULT"]["absolute_path"],
                "a",
                newline="",
            ) as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=ALL_FILES["VIEW_PARTITIONING_DELETE_RESULT"][
                        "columns"
                    ],
                )
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "removedPartition": True,
                }
                writer.writerow(values)

        # Tasks starten
        await self.run_async_tasks(
            views_to_delete_partition,
            remove_partitioning_for_view,
            thread_count
        )

        # Finales Logging mit Dateipfad
        file_name = ALL_FILES["VIEW_PARTITIONING_DELETE_RESULT"][
            "absolute_path"
        ]
        logger.info("Ergebnisse gespeichert in '%s'.", file_name)

    async def persist_views(
        self,
        thread_count: int = 1,
        timer: bool = False,
    ) -> None:
        """
        Persistiert Views. Threads können in geringer Anzahl genutzt werden,
        da es sonst zu Ratelimits kommen kann. Fünf Threads sind fehlerfrei
        durchgelaufen.
        Benötigt die Task-Datei VIEW_PERSIST_TASK_FILE_PATH.
        Schreibt Ergebnisse in VIEW_PERSIST_RESULT_FILE_PATH.

        Args:
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                          Anfragen. Standard ist 1.
            timer (bool, optional): Wenn True, wird die Dauer der Persistierung
                                    erfasst. Standard ist False.
        """

        # Task-Datei lesen
        views_to_persist = []
        with open(
            ALL_FILES["VIEW_PERSIST"]["absolute_path"], newline=""
        ) as file:
            reader = csv.DictReader(
                file,
                fieldnames=ALL_FILES["VIEW_PERSIST"]["columns"],
            )
            views_to_persist = list(reader)[1:]

        # Ergebnis-Datei mit Werten vorbefüllen
        with open(
            ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"],
            "a",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=ALL_FILES["VIEW_PERSIST_RESULT"]["columns"],
            )
            for view in views_to_persist:
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "isPersisted": False,
                    "runtime": None,
                }
                writer.writerow(values)

        # Headers anpassen
        # (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Priority")
        self.session.headers.pop("Origin")
        self.session.headers.update(
            {"Accept": "*/*", "x-request-id": str(uuid4()).replace("-", "")}
        )

        # Funktion um Result-Datei zu aktualisieren (erst gesamte Datei
        # einlesen, dann neu schreiben, um entsprechende Zeile zu
        # aktualisieren)
        def set_is_persisted_true(view_name: str, view_space: str) -> None:
            df = pd.read_csv(ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"])
            df.loc[
                (df["entity"] == view_name) & (df["space"] == view_space),
                "isPersisted",
            ] = True
            df.to_csv(
                ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"],
                index=False,
            )

        # Funktion um Result-Datei zu aktualisieren (erst gesamte Datei
        # einlesen, dann neu schreiben, um entsprechende Zeile zu
        # aktualisieren)
        def update_runtime(
            view_name: str, view_space: str, runtime: int
        ) -> None:
            df = pd.read_csv(
                ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"],
                dtype={"runtime": "Int64"},
            )
            df.loc[
                (df["entity"] == view_name) & (df["space"] == view_space),
                "runtime",
            ] = runtime
            df.to_csv(
                ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"],
                index=False,
            )

        # Tasks starten
        # TODO: wenn committed auch versuchen nochmal umzustellen
        # nested process_view Funktion die mit run_async_tasks aufgerufen 
        # werden kann erstellen
        if thread_count > 1:
            semaphore = asyncio.Semaphore(thread_count)
            tasks = []
            for view in views_to_persist:

                async def process_view(view):
                    async with semaphore:
                        success, log_details = await self._persist_view(
                            view["entity"], view["space"]
                        )
                        runtime = round(log_details.get("runTime", 0) / 1000)
                        if success:
                            set_is_persisted_true(
                                view["entity"], view["space"]
                            )
                            if timer and runtime > 0:
                                update_runtime(
                                    view["entity"], view["space"], runtime
                                )

                task = asyncio.create_task(process_view(view))
                tasks.append(task)
            await asyncio.gather(*tasks)

        else:
            for view in views_to_persist:
                success, log_details = await self._persist_view(
                    view["entity"], view["space"]
                )
                runtime = round(log_details.get("runTime", -1000) / 1000)
                if success:
                    set_is_persisted_true(view["entity"], view["space"])
                    if timer and runtime > 0:
                        update_runtime(view["entity"], view["space"], runtime)

        # Finales Logging mit Dateipfad
        file_name = ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"]
        logger.info("Ergebnisse gespeichert in '%s'.", file_name)

    async def _persist_view(
        self, view_name: str, view_space: str
    ) -> tuple[bool, dict]:
        """
        Persistiert eine View. Prüft dabei nicht, ob die View bereits
        persistiert ist.

        Args:
            view_name (str): Name der View.
            view_space (str): Name des View-Spaces.

        Returns:
            tuple[bool, dict]: True, wenn Persistierung erfolgreich war, sonst
                               False. Dict mit Log-Details.
        """

        # Persistenz starten
        logger.debug(
            "Starte Persistierung von View '%s' in '%s'...",
            view_name,
            view_space,
        )
        url = f"{DATASPHERE_URL}/dwaas-core/tf/directexecute"
        data = {
            "applicationId": "VIEWS",
            "spaceId": view_space,
            "objectId": view_name,
            "activity": "PERSIST",
        }
        response = await self.session.post(url=url, json=data)

        # Ergebnis prüfen und taskLogId parsen
        if response.status_code != 202:
            logger.error(
                "Fehler beim Starten der Persistierung für %s in %s. "
                "Wird übersprungen...",
                view_name,
                view_space,
            )
            return False, {}
        log_id = response.json()["taskLogId"]

        # Funktion zum Abrufen der Log-Details
        async def fetch_log_details() -> dict:
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )
            response = await self.session.get(
                url=(
                    f"{DATASPHERE_URL}/dwaas-core/tf/{view_space}/extendedlogs/{log_id}"
                )
            )
            return response.json()["logDetails"]

        # Ergebnisse abwarten
        log_details = {}
        while True:
            log_details = await fetch_log_details()
            latest_status = log_details["status"]
            if latest_status == "COMPLETED":
                break
            if latest_status == "FAILED" or (
                latest_status != "COMPLETED" and latest_status != "RUNNING"
            ):
                logger.error(
                    "Fehler beim Persistieren von %s in %s.",
                    view_name,
                    view_space,
                )
                return False, log_details

            # Laufzeit in lesbares Format umwandeln und ausgeben
            milliseconds = log_details["runTime"]
            hours, remainder = divmod(milliseconds, 3600000)
            minutes, seconds = divmod(remainder, 60000)
            seconds, milliseconds = divmod(seconds, 1000)
            logger.debug(
                "Warte auf Ergebnisse für %s in %s. "
                "Aktuelle Laufzeit: %02d:%02d:%02d.",
                view_name,
                view_space,
                hours,
                minutes,
                seconds,
            )
            await asyncio.sleep(1)

        # Result-Datei aktualisieren (erst gesamte Datei einlesen, dann neu
        # schreiben, um entsprechende Zeile zu aktualisieren)
        logger.info(
            "Persistierung für %s in %s abgeschlossen.", view_name, view_space
        )
        return True, log_details

    async def unpersist_views(self, thread_count: int = 1) -> None:
        """
        Entfernt Persistenzen für Views. Threads können in geringer Anzahl
        genutzt werden, da es sonst zu Ratelimits kommen kann.
        Fünf Threads sind fehlerfrei durchgelaufen.
        Benötigt die Task-Datei VIEW_UNPERSIST_TASK_FILE_PATH.
        Schreibt Ergebnisse in VIEW_UNPERSIST_RESULT_FILE_PATH.

        Args:
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                            Anfragen. Standard ist 1.
        """

        # Task-Datei lesen
        views_to_unpersist = []
        with open(
            ALL_FILES["VIEW_UNPERSIST"]["absolute_path"], newline=""
        ) as file:
            reader = csv.DictReader(
                file,
                fieldnames=ALL_FILES["VIEW_UNPERSIST"]["columns"],
            )
            views_to_unpersist = list(reader)[1:]

        # Ergebnis-Datei mit Werten vorbefüllen
        with open(
            ALL_FILES["VIEW_UNPERSIST_RESULT"]["absolute_path"],
            "a",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=ALL_FILES["VIEW_UNPERSIST_RESULT"]["columns"],
            )
            for view in views_to_unpersist:
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "isRemoved": False,
                }
                writer.writerow(values)

        # Headers anpassen
        # (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Priority")
        self.session.headers.pop("Origin")
        self.session.headers.update(
            {"Accept": "*/*", "x-request-id": str(uuid4()).replace("-", "")}
        )

        # Funktion, um nur entsprechende Zeile in Result-Datei zu aktualisieren
        def set_is_removed_true(view_name: str, view_space: str) -> None:
            """
            Setzt isRemoved in der Result-Datei für die aktuelle View auf True.
            """
            df = pd.read_csv(
                ALL_FILES["VIEW_UNPERSIST_RESULT"]["absolute_path"]
            )
            df.loc[
                (df["entity"] == view_name) & (df["space"] == view_space),
                "isRemoved",
            ] = True
            df.to_csv(
                ALL_FILES["VIEW_UNPERSIST_RESULT"]["absolute_path"],
                index=False,
            )

        # Tasks starten
        if thread_count > 1:
            semaphore = asyncio.Semaphore(thread_count)
            tasks = []
            for view in views_to_unpersist:

                async def process_view(view):
                    async with semaphore:
                        success, _ = await self._unpersist_view(
                            view["entity"], view["space"]
                        )
                        if success:
                            set_is_removed_true(view["entity"], view["space"])

                task = asyncio.create_task(process_view(view))
                tasks.append(task)
            await asyncio.gather(*tasks)

        else:
            for view in views_to_unpersist:
                success, _ = await self._unpersist_view(
                    view["entity"], view["space"]
                )
                if success:
                    set_is_removed_true(view["entity"], view["space"])

        # Finales Logging mit Dateipfad
        file_name = ALL_FILES["VIEW_UNPERSIST_RESULT"]["absolute_path"]
        logger.info("Ergebnisse gespeichert in '%s'.", file_name)

    async def _unpersist_view(
        self, view_name: str, view_space: str
    ) -> tuple[bool, dict]:
        """
        Entfernt die Persistenz für eine View. Prüft vorher, ob View
        persistiert ist.

        Args:
            view_name (str): Name der View.
            view_space (str): Name des View-Spaces.

        Returns:
            tuple[bool, dict]: True, wenn Entfernung der Persistenz erfolgreich
                               war, sonst False. Dict mit Log-Details.
        """

        # Prüfen, ob View persistiert ist
        url = (
            f"{DATASPHERE_URL}/dwaas-core/monitor/{view_space}"
            f"/persistedViews/{view_name}"
        )
        response = await self.session.get(url=url)
        if (
            response.status_code != 200
            or "dataPersistency" not in response.json()
        ):
            logger.error(
                "Fehler beim Prüfen, ob View '%s' in '%s' persistiert ist. "
                "Statuscode: %s. Wird übersprungen...",
                view_name,
                view_space,
                response.status_code,
            )
            return False, {}
        if response.json()["dataPersistency"] != "Persisted":
            logger.debug(
                "View '%s' in '%s' ist nicht persistiert. "
                "Wird übersprungen...",
                view_name,
                view_space,
            )
            return True, {}

        # Persistenz entfernen
        logger.debug(
            "Entferne Persistenz für '%s' in '%s'...", view_name, view_space
        )
        self.session.headers.update(
            {"x-request-id": str(uuid4()).replace("-", "")}
        )
        url = f"{DATASPHERE_URL}/dwaas-core/tf/directexecute"
        data = {
            "applicationId": "VIEWS",
            "spaceId": view_space,
            "objectId": view_name,
            "activity": "REMOVE_PERSISTED_DATA",
        }
        response = await self.session.post(url=url, json=data)

        # Ergebnis prüfen und taskLogId parsen
        if response.status_code != 202:
            logger.error(
                "Fehler beim Entfernen der Persistenz für '%s' in '%s'. "
                "Wird übersprungen...",
                view_name,
                view_space,
            )
            return False, {}
        log_id = response.json()["taskLogId"]

        # Funktion zum Abrufen der Log-Details
        async def fetch_log_details() -> dict:
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )
            response = await self.session.get(
                url=(
                    f"{DATASPHERE_URL}/dwaas-core/tf"
                    f"/{view_space}/extendedlogs/{log_id}"
                )
            )
            return response.json()["logDetails"]

        # Ergebnisse abwarten
        log_details = {}
        while True:
            log_details = await fetch_log_details()
            latest_status = log_details["status"]
            if latest_status == "COMPLETED":
                break
            if latest_status == "FAILED" or (
                latest_status != "COMPLETED" and latest_status != "RUNNING"
            ):
                logger.error(
                    "Fehler beim Entfernen der Persistenz für '%s' in '%s'.",
                    view_name,
                    view_space,
                )
                return False, log_details

            # Laufzeit in lesbares Format umwandeln und ausgeben
            milliseconds = log_details["runTime"]
            hours, remainder = divmod(milliseconds, 3600000)
            minutes, seconds = divmod(remainder, 60000)
            seconds, milliseconds = divmod(seconds, 1000)
            logger.debug(
                f"Warte auf Ergebnisse für '{view_name}' in '{view_space}'. "
                f"Aktuelle Laufzeit: {hours:02}:{minutes:02}:{seconds:02}."
            )
            await asyncio.sleep(1)

        # Result-Datei aktualisieren
        logger.info(
            "Persistenz für '%s' in '%s' entfernt.", view_name, view_space
        )
        return True, log_details

    async def lock_partitions_until_year(self, year: int,
    thread_count: int = 1) -> None:
        """
        Sperrt Partitionen für alle Views, die in der
        Datei 'views_to_lock_partitions.csv' enthalten sind. Überspringt Views,
        die keine Partitionen haben.
        Alle Partitionen MÜSSEN ganzzahlige Werte sein!!
        Benötigt die Task-Datei VIEW_PARTITION_LOCK_FILE_PATH.
        Schreibt Ergebnisse in VIEW_PARTITION_LOCK_RESULT_FILE_PATH.

        Args:
            year (int): Jahr, bis zu dem Partitionen gesperrt werden
                        sollen (einschließlich des Jahres selbst).
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                          Anfragen. Standard ist 1.
        """

        # Task-Datei lesen
        views_to_lock = []
        with open(
            ALL_FILES["VIEW_PARTITION_LOCK"]["absolute_path"],
            newline="",
        ) as file:
            reader = csv.DictReader(
                file,
                fieldnames=ALL_FILES["VIEW_PARTITION_LOCK"]["columns"],
            )
            views_to_lock = list(reader)[1:]

        # Headers anpassen
        # (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Origin")
        self.session.headers.pop("Priority")
        self.session.headers.update({"Accept": "*/*"})

        # Funktion, um Partitionen zu sperren
        async def lock_partitions_for_view(view) -> None:
            # Prüfen, ob Partition bereits existiert
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )
            response = await self.session.get(
                url=f"{DATASPHERE_URL}/dwaas-core/partitioning"
                f"/{view['space']}/persistedViews/{view['entity']}"
            )
            partition_exists = len(response.json()["ranges"]) > 0

            # Fehler prüfen
            if not partition_exists:
                logger.error(
                    "View %s in %s hat keine Partitionen. "
                    "Wird übersprungen...",
                    view["entity"],
                    view["space"],
                )
                return

            # Daten der View abrufen
            view_data = response.json()

            # Partitionen sperren
            logger.debug(
                "Sperre Partitionen für %s in %s bis einschließlich "
                "Jahr %s...",
                view["entity"],
                view["space"],
                year,
            )
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )
            url = (
                f"{DATASPHERE_URL}/dwaas-core/partitioning/{view['space']}"
                f"/persistedViews/{view['entity']}"
            )
            data = {
                "remoteSourceName": view_data["remoteSourceName"],
                "objectName": view_data["objectName"],
                "numParallelPartitions": view_data["numParallelPartitions"],
                "ranges": view_data["ranges"],
                "column": view_data["column"],
                "columnType": view_data["columnType"],
                "runtimeDataCalculation": view_data["runtimeDataCalculation"],
                "type": view_data["type"],
            }
            for partition in data["ranges"]:
                if int(partition["low"]["value"]) <= year:
                    partition["locked"] = True
            response = await self.session.post(url=url, json=data)

            # In Datei vermerken
            if response.status_code == 201:
                logger.info(
                    "Partitionen für %s in %s wurden bis einschließlich "
                    "Jahr %s gesperrt.",
                    view["entity"],
                    view["space"],
                    year,
                )
            else:
                logger.error(
                    "Fehler beim Sperren der Partitionen für %s in %s.",
                    view["entity"],
                    view["space"],
                )
                logger.debug("Response: %s\n", response.text)
            with open(
                ALL_FILES["VIEW_PARTITION_LOCK_RESULT"]["absolute_path"],
                "a",
                newline="",
            ) as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=ALL_FILES["VIEW_PARTITION_LOCK_RESULT"][
                        "columns"
                    ],
                )
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "lockedPartitions": response.status_code == 201,
                }
                writer.writerow(values)

        # Tasks starten
        await self.run_async_tasks(
            views_to_lock,
            lock_partitions_for_view,
            thread_count
        )

        # Finales Logging mit Dateipfad
        file_name = ALL_FILES["VIEW_PARTITION_LOCK_RESULT"]["absolute_path"]
        logger.info("Ergebnisse gespeichert in '%s'.", file_name)

    async def unlock_all_partitions(self,
    thread_count: int = 1) -> None:
        """
        Entsperrt alle Partitionen für alle Views,
        die in der Datei 'views_to_unlock_partitions.csv' enthalten sind.
        Benötigt die Task-Datei VIEW_PARTITION_UNLOCK_FILE_PATH.
        Schreibt Ergebnisse in VIEW_PARTITION_UNLOCK_RESULT_FILE_PATH.

        Args:
            thread_count (int, optional): Anzahl an gleichzeitigen, asynchronen
                                          Anfragen. Standard ist 1.
        """

        # Task-Datei lesen
        views_to_unlock = []
        with open(
            ALL_FILES["VIEW_PARTITION_UNLOCK"]["absolute_path"],
            newline="",
        ) as file:
            reader = csv.DictReader(
                file,
                fieldnames=ALL_FILES["VIEW_PARTITION_UNLOCK"]["columns"],
            )
            views_to_unlock = list(reader)[1:]

        # Headers anpassen
        # (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Origin")
        self.session.headers.pop("Priority")
        self.session.headers.update({"Accept": "*/*"})

        # Funktion, um Partitionen zu entsperren
        async def unlock_partitions_for_view(view) -> None:
            # Prüfen, ob Partition bereits existiert
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )
            response = await self.session.get(
                url=f"{DATASPHERE_URL}/dwaas-core/partitioning"
                f"/{view['space']}/persistedViews/{view['entity']}"
            )
            partition_exists = len(response.json()["ranges"]) > 0

            # Fehler prüfen
            if not partition_exists:
                logger.error(
                    "View %s in %s hat keine Partitionen. "
                    "Wird übersprungen...",
                    view["entity"],
                    view["space"],
                )
                return

            # Daten der View abrufen
            view_data = response.json()

            # Partitionen entsperren
            logger.debug(
                "Entsperre alle Partitionen für %s in %s...",
                view["entity"],
                view["space"],
            )
            self.session.headers.update(
                {"x-request-id": str(uuid4()).replace("-", "")}
            )
            url = (
                f"{DATASPHERE_URL}/dwaas-core/partitioning/{view['space']}"
                f"/persistedViews/{view['entity']}"
            )
            data = {
                "remoteSourceName": view_data["remoteSourceName"],
                "objectName": view_data["objectName"],
                "numParallelPartitions": view_data["numParallelPartitions"],
                "ranges": view_data["ranges"],
                "column": view_data["column"],
                "columnType": view_data["columnType"],
                "runtimeDataCalculation": view_data["runtimeDataCalculation"],
                "type": view_data["type"],
            }
            for partition in data["ranges"]:
                partition["locked"] = False
            response = await self.session.post(url=url, json=data)

            # In Datei vermerken
            if response.status_code == 201:
                logger.info(
                    "Partitionen für %s in %s wurden entsperrt.",
                    view["entity"],
                    view["space"],
                )
            else:
                logger.error(
                    "Fehler beim Entsperren der Partitionen für %s in %s.",
                    view["entity"],
                    view["space"],
                )
                logger.debug("Response: %s\n", response.text)
            with open(
                ALL_FILES["VIEW_PARTITION_UNLOCK_RESULT"]["absolute_path"],
                "a",
                newline="",
            ) as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=ALL_FILES["VIEW_PARTITION_UNLOCK_RESULT"][
                        "columns"
                    ],
                )
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "unlockedPartitions": response.status_code == 201,
                }
                writer.writerow(values)

        # Tasks starten
        await self.run_async_tasks(
            views_to_unlock,
            unlock_partitions_for_view,
            thread_count
        )

        # Finales Logging mit Dateipfad
        file_name = ALL_FILES["VIEW_PARTITION_UNLOCK_RESULT"]["absolute_path"]
        logger.info("Ergebnisse gespeichert in '%s'.", file_name)
