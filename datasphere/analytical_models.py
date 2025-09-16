import concurrent.futures
import csv
import json
import threading
from copy import deepcopy
from time import sleep
from urllib.parse import quote, urlencode
from uuid import uuid4

import requests

from datasphere.automation import DatasphereAutomation
from datasphere.views import Views
from utils.filehandler import Datasphere, settings
from utils.logging import logger
from utils.types import AnalyticalModelsDetailsDict

# Wichtige Bedingungen aus Settings
URL_TO_USE: str = settings["Setup"]["URL_TO_USE"]

# Wichtige URLs aus Settings
DATASPHERE_URL: str = settings["URLs"][URL_TO_USE]


class AnalyticalModels(DatasphereAutomation):
    def __init__(self, session: requests.Session | None = None):
        # DatasphereAutomation initialisieren
        super().__init__(session)

    def _get_all_analytical_models(self) -> list[AnalyticalModelsDetailsDict]:
        """
        Gibt alle Analytical Models als Liste von Dictionaries zurück.

        Returns:
            list[AnalyticalModelsDetailsDict]: Liste von Dictionaries mit den
                                               Analytical Models.
        """

        # Headers anpassen
        self.session.headers.pop("X-Csrf-Token")
        self.session.headers.pop("X-Requested-With")
        self.session.headers.pop("Priority")
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

        # Alle Analytical Models abrufen
        url = f"{DATASPHERE_URL}/deepsea/repository/search/$all"
        params = {
            "$top": 1000,  # kann nicht weggelassen werden
            "$skip": 0,
            "whyfound": "true",
            "$count": "true",
            "valuehierarchy": "folder_id",
            "facets": "all",
            "facetlimit": 5,
            "$apply": (
                "filter(Search.search(query='SCOPE:SEARCH_DESIGN "
                '(technical_type_description:EQ(S):"Analysemodell" AND '
                '(technical_type:EQ(S):"DWC_REMOTE_TABLE" OR technical_type:'
                'EQ(S):"DWC_LOCAL_TABLE" OR technical_type:EQ(S):"DWC_VIEW" '
                'OR technical_type:EQ(S):"DWC_ERMODEL" OR technical_type:'
                'EQ(S):"DWC_DATAFLOW" OR technical_type:EQ(S):"DWC_IDT" OR '
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
        response = self.session.get(
            url=url, params=urlencode(params, safe="()*", quote_via=quote)
        )
        all_analytical_models: list[AnalyticalModelsDetailsDict] = (
            response.json()["value"]
        )

        # Nicht benötigte Headers für weitere Requests wieder entfernen
        self.session.headers.pop("Origin")
        self.session.headers.pop("UI5-Timezone")
        self.session.headers.pop("UI5-Timepattern")
        self.session.headers.pop("UI5-Datepattern")
        self.session.headers.pop("Cache-Control")

        return all_analytical_models

    def _get_all_analytical_models_from_space(
        self, space_name: str
    ) -> list[AnalyticalModelsDetailsDict]:
        """
        Gibt alle Analytical Models in einem bestimmten Space zurück.

        Args:
            space_name (str): Name des Spaces.

        Returns:
            list[AnalyticalModelsDetailsDict]: Liste von Dictionaries mit den
                                               Analytical Models.
        """

        # Alle analytischen Modelle abrufen
        all_analytical_models_in_space = [
            model
            for model in self._get_all_analytical_models()
            if model["space_name"] == space_name
        ]
        return all_analytical_models_in_space

    def _get_all_views_for_analytical_model(
        self, analytical_model_id: str
    ) -> dict[str, dict[str, str]]:
        """
        Gibt alle Views zurück, die in einem Analytical Model genutzt werden.

        Args:
            analytical_model_id (str): ID des Analytical Models.

        Returns:
            dict[str, dict[str, str]]: Dictionary mit Analytical Model-ID als
                                       Schlüssel und Dictionary als Wert.
                                       Dieses Dictionary hat als Schlüssel die
                                       IDs der Views und als Wert den Namen der
                                       Views.
        """

        # Headers updaten
        # (Voraussetzung: vorher wurde get_all_analytical_models() aufgerufen)
        self.session.headers.update(
            {"Accept": "*/*", "x-request-id": str(uuid4()).replace("-", "")}
        )

        # Details abrufen
        url = f"{DATASPHERE_URL}/deepsea/repository/dependencies/"
        params = {
            "ids": analytical_model_id,
            "recursive": True,
            "impact": True,
            "lineage": True,
            "details": (
                "#spaceName,#spaceLabel,qualified_name,@EndUserText.label,"
                "@EnterpriseSearch.enabled,owner,deployment_date,"
                "modification_date,#objectStatus,#businessType,#technicalType,"
                "@Analytics.provider,#isViewEntity,"
                "@DataWarehouse.remote.connection,#isToolingHidden,"
                "releaseStateValue,releaseDate,deprecationDate,"
                "decommissioningDate,@ObjectModel.supportedCapabilities,"
                "@DataWarehouse.consumption.external,#columnsCount,"
                "@Analytics.dbViewType,isMissingColumnLineage"
            ),
            "dependencyTypes": (
                "csn.query.from,sap.dis.source,sap.dis.targetOf,"
                "sap.dis.replicationflow.source,"
                "sap.dis.replicationflow.targetOf,"
                "sap.dwc.transformationflow.source,"
                "sap.dwc.transformationflow.targetOf,sap.dwc.idtEntity,"
                "csn.derivation.lookupEntity,csn.valueHelp.entity"
            ),
        }
        response = self.session.get(url=url, params=params)
        model_details = response.json()[0]

        # Funktion zur rekursiven Iteration implementieren
        all_ids: list[tuple[str, str]] = []

        def iterate_recursively(entity: dict):
            if entity["properties"].get("#isViewEntity", "false") == "true":
                all_ids.append((entity["id"], entity["name"]))
            if len(entity["dependencies"]) > 0:
                for dependency in entity["dependencies"]:
                    iterate_recursively(dependency)

        # Über alle Dependencies iterieren
        iterate_recursively(model_details)

        # Liste umdrehen, für Bottom-Up-Reihenfolge
        all_ids.reverse()
        analytical_model_to_view_mapping = {
            analytical_model_id: {val[0]: val[1] for val in all_ids}
        }
        return analytical_model_to_view_mapping

    def get_all_views_for_analytical_models(
        self, skip_duplicates: bool = False
    ) -> None:
        """
        Speichert alle analytischen Modelle mit den dazugehörigen Views in
        einer Datei ab.
        Die Datei hat folgende Struktur:
        {
            "ID des Analytischen Modells":
                {
                    "name": "Name des Analytischen Modells",
                    "dependencies":
                        {
                            "ID der View": "Name der View",
                            ...
                    }
            }
        }

        Args:
            skip_duplicates (bool, optional): Wenn True, werden Views
                                              herausgefiltert, die schon in
                                              anderen Analytical Models
                                              vorkommen. Standard ist False.
                                              Dieses Feature kann z.B. genutzt
                                              werden, um Aufgabenketten zu
                                              planen.
        """

        # Alle analytischen Modelle abrufen
        logger.debug("Lade alle Analytischen Modelle...")
        all_analytical_models = self._get_all_analytical_models()
        analytical_models_with_views = {}

        # Alle Views abrufen
        views = Views(self.session)
        all_views_list = [
            (view["id"], view["space_name"]) for view in views._get_all_views()
        ]

        # Über alle Modelle iterieren
        for model in all_analytical_models:
            logger.debug(
                "Lade alle Views für das Analytische Modell '%s' in '%s'...",
                model["name"],
                model["space_name"],
            )
            all_views = self._get_all_views_for_analytical_model(model["id"])

            # Views herausfiltern, die schon in anderen Modellen vorkommen,
            # wenn skip_duplicates = True
            if skip_duplicates:
                for view in deepcopy(all_views[model["id"]]):
                    for saved_model in analytical_models_with_views:
                        if (
                            view
                            in analytical_models_with_views[saved_model][
                                "dependencies"
                            ]
                        ):
                            all_views[model["id"]].pop(view)
                            break

            # Analytisches Modell abspeichern
            analytical_models_with_views[model["id"]] = {
                "name": model["name"],
                "dependencies": all_views[model["id"]],
            }

            # Spaces der Views herausfiltern
            logger.debug("Update Views mit Spaces...")
            for view_id, view_name in analytical_models_with_views[
                model["id"]
            ]["dependencies"].items():
                for view in all_views_list:
                    if view_id == view[0]:
                        analytical_models_with_views[model["id"]][
                            "dependencies"
                        ][view_id] = (view[1], view_name)
                        break

        # Ergebnis speichern
        with open(
            Datasphere.ALL_FILES["ANALYTICAL_MODELS_ALL_VIEWS"][
                "absolute_path"
            ],
            "w",
        ) as file:
            json.dump(analytical_models_with_views, file, indent=4)
        logger.info(
            "Ergebnisse gespeichert in '%s'.",
            Datasphere.ALL_FILES["ANALYTICAL_MODELS_ALL_VIEWS"][
                "absolute_path"
            ],
        )

    def get_all_views_for_analytical_models_in_space(
        self, space_name: str, skip_duplicates: bool = False
    ) -> None:
        """
        Speichert alle analytischen Modelle eines bestimmten Spaces mit den
        dazugehörigen Views in einer Datei ab.
        Die Datei hat folgende Struktur:
        {
            "ID des Analytischen Modells":
                {
                    "name": "Name des Analytischen Modells",
                    "dependencies":
                        {
                            "ID der View": [
                                "Space der View",
                                "Name der View"
                            ], ...
                    }
            }
        }

        Args:
            space_name (str): Name des Spaces.
            skip_duplicates (bool, optional): Wenn True, werden Views
                                              herausgefiltert, die schon in
                                              anderen Analytical Models
                                              vorkommen. Standard ist False.
                                              Dieses Feature kann z.B. genutzt
                                              werden, um Aufgabenketten zu
                                              planen.
        """

        # Alle analytischen Modelle abrufen
        logger.debug(
            "Lade alle Analytischen Modelle aus dem Space '%s'...",
            space_name,
        )
        all_analytical_models_in_space = (
            self._get_all_analytical_models_from_space(space_name)
        )

        # Alle Views abrufen
        views = Views(self.session)
        all_views_list = [
            (view["id"], view["space_name"]) for view in views._get_all_views()
        ]

        # Über alle Modelle iterieren
        analytical_models_with_views_in_space = {}
        for model in all_analytical_models_in_space:
            logger.debug(
                "Lade alle Views für das Analytische Modell '%s'...",
                model["name"],
            )
            all_views = self._get_all_views_for_analytical_model(model["id"])

            # Views herausfiltern, die schon in anderen Modellen vorkommen,
            # wenn skip_duplicates = True
            if skip_duplicates:
                logger.debug("Filtere bereits gefundene Views heraus...")
                for view in deepcopy(all_views[model["id"]]):
                    for saved_model in analytical_models_with_views_in_space:
                        if (
                            view
                            in analytical_models_with_views_in_space[
                                saved_model
                            ]["dependencies"]
                        ):
                            all_views[model["id"]].pop(view)
                            break

            # Analytisches Modell abspeichern
            analytical_models_with_views_in_space[model["id"]] = {
                "name": model["name"],
                "dependencies": all_views[model["id"]],
            }

            # Spaces der Views herausfiltern
            logger.debug("Update Views mit ihren Spaces...")
            for view_id, view_name in analytical_models_with_views_in_space[
                model["id"]
            ]["dependencies"].items():
                for view in all_views_list:
                    if view_id == view[0]:
                        analytical_models_with_views_in_space[model["id"]][
                            "dependencies"
                        ][view_id] = (view[1], view_name)
                        break

        # Ergebnis speichern
        file_name = Datasphere.ALL_FILES[
            "ANALYTICAL_MODELS_ALL_VIEWS_IN_SPACE"
        ]["absolute_path"].replace("space", space_name)
        with open(file_name, "w") as file:
            json.dump(analytical_models_with_views_in_space, file, indent=4)
        logger.info("Ergebnisse gespeichert in '%s'.", file_name)

    def check_runtime_for_all_views_of_analytical_models(
        self, use_threads: bool = True, thread_count: int = 1
    ) -> None:
        """
        Prüft die Laufzeit aller Views für die Analytischen Modelle, die in der
        Datei ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME gespeichert sind.
        Persistiert dafür die Views und entpersistiert anschließend wieder die
        Views, die nicht bereits persistiert waren.
        Speichert das Ergebnis in der Datei
        ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME_RESULT.

        Args:
            use_threads (bool, optional): Wenn True, werden die Tasks parallel
                                          ausgeführt. Standard ist True.
            thread_count (int, optional): Anzahl der Threads, die parallel
                                          ausgeführt werden sollen.
                                          Standard ist 1.
        """

        # Task-Datei lesen
        models_to_check = []
        file_name = Datasphere.ALL_FILES[
            "ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME"
        ]["absolute_path"]
        with open(file_name, newline="") as file:
            reader = csv.DictReader(
                file,
                fieldnames=Datasphere.ALL_FILES[
                    "ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME"
                ]["columns"],
            )
            models_to_check = list(reader)[1:]

        # Alle Analytischen Modelle abrufen und Mapping von ID erstellen
        # (brauche ich für Methode)
        logger.debug("Lade alle Analytischen Modelle...")
        all_analytical_models = self._get_all_analytical_models()
        models_mapping_id_to_name_and_space = {
            model["id"]: (model["name"], model["space_name"])
            for model in all_analytical_models
        }

        # Alle Views abrufen
        views = Views(self.session)
        all_views_list = [
            (view["id"], view["space_name"]) for view in views._get_all_views()
        ]

        # Views für alle Analytischen Modelle abrufen
        analytical_models_with_views = {}
        for model in models_to_check:
            # ID des Analytischen Modells aus dem ID-zu-Namen-und-Space-Mapping
            # filtern
            found = False
            for model_id, (
                name,
                space,
            ) in models_mapping_id_to_name_and_space.items():
                if model["modelname"] == name and model["space"] == space:
                    found = True

                    # Alle Views für das Analytische Modell abrufen
                    logger.debug(
                        "Lade alle Views für das Analytische Modell '%s'...",
                        model["modelname"],
                    )
                    all_views = self._get_all_views_for_analytical_model(
                        model_id
                    )

                    # Views herausfiltern, die schon in anderen Modellen
                    # vorkommen, wenn skip_duplicates = True
                    for view in deepcopy(all_views[model_id]):
                        for saved_model in analytical_models_with_views:
                            if (
                                view
                                in analytical_models_with_views[saved_model][
                                    "dependencies"
                                ]
                            ):
                                all_views[model_id].pop(view)
                                break

                    # Analytisches Modell abspeichern
                    analytical_models_with_views[model_id] = {
                        "name": model["modelname"],
                        "dependencies": all_views[model_id],
                    }

                    # Spaces der Views herausfiltern
                    logger.debug("Update Views mit ihren Spaces...")
                    for view_id, view_name in analytical_models_with_views[
                        model_id
                    ]["dependencies"].items():
                        for view in all_views_list:
                            if view_id == view[0]:
                                analytical_models_with_views[model_id][
                                    "dependencies"
                                ][view_id] = (view[1], view_name)
                                break
                    break

            # Prüfen, ob Analytisches Modell gefunden wurde
            if not found:
                logger.error(
                    "Analytisches Modell '%s' im Space '%s' wurde nicht "
                    "gefunden.",
                    model["modelname"],
                    model["space"],
                )

        # Alle Views als Liste filtern
        all_views_to_persist = []
        for model_id, model_data in analytical_models_with_views.items():
            for view_id, (view_space, view_name) in model_data[
                "dependencies"
            ].items():
                all_views_to_persist.append(
                    (model_id, view_id, view_space, view_name)
                )

        # Analytische Modelle formatieren und Runtime=0 setzen
        analytical_models_with_views_readable = {}
        for model_id, model_data in analytical_models_with_views.items():
            analytical_models_with_views_readable[model_id] = {
                "name": model_data["name"],
                "dependencies": {
                    view_id: {
                        "space": view_space,
                        "name": view_name,
                        "runtime": None,
                        "alreadyPersisted": False,
                        "removedPersistency": False,
                    }
                    for view_id, (view_space, view_name) in model_data[
                        "dependencies"
                    ].items()
                },
            }

        # Funktion um Runtime zur View hinzuzufügen
        def update_runtime(
            model_id: str,
            view_id: str,
            runtime: int | None,
            lock: threading.Lock | None = None,
        ) -> None:
            if lock:
                lock.acquire()
            analytical_models_with_views_readable[model_id]["dependencies"][
                view_id
            ]["runtime"] = runtime
            if lock:
                lock.release()

        # Funktion um Analytische Modelle zu speichern
        file_path_results = Datasphere.ALL_FILES[
            "ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME_RESULT"
        ]["absolute_path"]

        def save_results(lock: threading.Lock | None = None) -> None:
            if lock:
                lock.acquire()
            with open(file_path_results, "w") as file:
                json.dump(
                    analytical_models_with_views_readable, file, indent=4
                )
            if lock:
                lock.release()

        # Funktion, um Persistenz zu prüfen
        def check_if_persisted(
            session: requests.Session, view_name: str, view_space: str
        ) -> bool:
            url = (
                f"{DATASPHERE_URL}/dwaas-core/monitor/{view_space}/"
                f"persistedViews/{view_name}"
            )
            for _ in range(3):
                response = session.get(url=url)
                if response.status_code != 200:
                    sleep(3)
                    continue
                return (
                    response.json().get("dataPersistency", "") == "Persisted"
                )
            else:
                raise RuntimeError(
                    f"Persistenz der View '{view_name}' in '{view_space}' "
                    "konnte nicht geprüft werden."
                )

        # Bei allen Views prüfen, ob bereits persistiert
        logger.debug("Prüfe, ob Views bereits persistiert sind...")
        for model_data in analytical_models_with_views_readable.values():
            for view_data in model_data["dependencies"].values():
                if check_if_persisted(
                    self.session, view_data["name"], view_data["space"]
                ):
                    view_data["alreadyPersisted"] = True

        # Erstes Mal Ergebnisse speichern
        logger.debug("Speichere Ergebnisse...")
        save_results()

        # Funktion, für Persistierung und anschließendes Entfernen der
        # Persistenz
        def persist_and_unpersist_view(
            session: requests.Session,
            model_id: str,
            view_id: str,
            view_space: str,
            view_name: str,
            lock: threading.Lock | None = None,
        ) -> None:
            # Persistierung starten
            logger.debug(
                "Starte Persistierung von View '%s' in '%s'...",
                view_name,
                view_space,
            )
            persisted, log_details = views._persist_view(
                session, view_name, view_space
            )
            runtime = round(log_details.get("runTime", -1000) / 1000)

            # Speichern bei erfolgreicher Persistierung
            if persisted:
                logger.info(
                    "View '%s' in '%s' wurde persistiert.",
                    view_name,
                    view_space,
                )
                update_runtime(
                    model_id, view_id, runtime if runtime > 0 else None, lock
                )
                save_results(lock)

                # Persistenz entfernen, wenn nicht vorher persistiert war
                if not analytical_models_with_views_readable[model_id][
                    "dependencies"
                ][view_id]["alreadyPersisted"]:
                    logger.debug(
                        "Entferne Persistenz von View '%s' in '%s'...",
                        view_name,
                        view_space,
                    )
                    unpersisted, _ = views._unpersist_view(
                        session, view_name, view_space
                    )

                    # Speichern bei erfolgreicher Entpersistierung
                    if unpersisted:
                        logger.info(
                            "Persistenz von View '%s' in '%s' wurde entfernt.",
                            view_name,
                            view_space,
                        )
                        if lock:
                            lock.acquire()
                        analytical_models_with_views_readable[model_id][
                            "dependencies"
                        ][view_id]["removedPersistency"] = True
                        if lock:
                            lock.release()
                        save_results(lock)

                    else:
                        logger.critical(
                            "Persistenz von View '%s' in '%s' konnte nach "
                            "erfolgreicher Persistierung nicht entfernt "
                            "werden.",
                            view_name,
                            view_space,
                        )
                        logger.critical("Bitte überprüfen.")

                else:
                    logger.debug(
                        "View '%s' in '%s' war bereits persistiert. "
                        "Persistenz wird nicht entfernt.",
                        view_name,
                        view_space,
                    )
                    update_runtime(
                        model_id,
                        view_id,
                        runtime if runtime > 0 else None,
                        lock,
                    )
                    save_results(lock)

            else:
                logger.critical(
                    "View '%s' in '%s' konnte nicht persistiert werden.",
                    view_name,
                    view_space,
                )
                logger.critical(
                    "Bitte überprüfen, ob die View trotzdem persistiert wurde."
                )

        # Tasks starten und Zeit loggen
        logger.debug("Starte Tasks...")
        if use_threads:
            lock = threading.Lock()
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=thread_count
            ) as executor:
                for view in all_views_to_persist:
                    executor.submit(
                        persist_and_unpersist_view,
                        deepcopy(self.session),
                        *view,
                        lock=lock,
                    )

        else:
            for view in all_views_to_persist:
                persist_and_unpersist_view(self.session, *view)
