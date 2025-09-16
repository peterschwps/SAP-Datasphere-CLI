import configparser
import csv
import os.path
import sys

from utils.logging import logger

# Pfade
PROJECT_PATH = os.getcwd()
COOKIES_FILE = os.path.join(PROJECT_PATH, ".cookies.json")
SETTINGS_FILE = os.path.join(PROJECT_PATH, "settings.ini")

# Format der Settings-Datei
settings = configparser.ConfigParser()
settings.optionxform = str  # pyright: ignore[reportAttributeAccessIssue]

# Settings erstellen, falls nicht vorhanden
# TODO: anpassen siehe MentAI (mit allgemeinen User Config Path und ispath() Check)  # noqa: E501
if not os.path.isfile(SETTINGS_FILE):
    settings["URLs"] = {
        # System > Administration > Tenant Links: SAP Datasphere URL
        "DATASPHERE_TEST_URL": "https://example-test.eu10.hcs.cloud.sap",
        # System > Administration > Tenant Links: SAP Datasphere URL
        "DATASPHERE_PROD_URL": "https://example-prod.eu10.hcs.cloud.sap",
    }
    settings["Setup"] = {
        # DATASPHERE_TEST_URL oder DATASPHERE_PROD_URL
        "URL_TO_USE": "DATASPHERE_PROD_URL",
        # REQUESTS oder BROWSER
        "AUTHENTICATION_METHOD": "REQUESTS",
    }
    with open(SETTINGS_FILE, "w") as settings_file:
        settings.write(settings_file)
    logger.info("Settings Datei erstellt: %s", SETTINGS_FILE)
    logger.debug("Bitte befüllen und Programm erneut starten...")
    sys.exit()

# Sonst Settings laden
else:
    settings.read(SETTINGS_FILE)


class Datasphere:
    # Ordner
    ALL_PATHS = {
        "EXPORTS": "datasphere/exports",
        "RESULTS": "datasphere/results",
        "TASKS": "datasphere/tasks",
    }

    # Dateien
    ALL_FILES = {
        "VIEW_ANALYSE": {
            "name": "best_views_to_persist.csv",
            "path": ALL_PATHS["EXPORTS"],
            "columns": ["entity", "space", "businessName", "isPersisted"],
        },
        "VIEW_ATTRIBUTE": {
            "name": "view_attributes.csv",
            "path": ALL_PATHS["EXPORTS"],
            "columns": ["entity", "space", "businessName", "attribute"],
        },
        "VIEW_PARTITIONING_CREATE": {
            "name": "views_to_create_partitions.csv",
            "path": ALL_PATHS["TASKS"],
            "columns": ["entity", "space", "attribute"],
        },
        "VIEW_PARTITIONING_CREATE_RESULT": {
            "name": "views_partitions_created.csv",
            "path": ALL_PATHS["RESULTS"],
            "columns": ["entity", "space", "attribute", "createdPartition"],
        },
        "VIEW_PARTITIONING_DELETE": {
            "name": "views_to_delete_partitions.csv",
            "path": ALL_PATHS["TASKS"],
            "columns": ["entity", "space"],
        },
        "VIEW_PARTITIONING_DELETE_RESULT": {
            "name": "views_partitions_deleted.csv",
            "path": ALL_PATHS["RESULTS"],
            "columns": ["entity", "space", "removedPartition"],
        },
        "VIEW_PERSIST": {
            "name": "views_to_persist.csv",
            "path": ALL_PATHS["TASKS"],
            "columns": ["entity", "space"],
        },
        "VIEW_PERSIST_RESULT": {
            "name": "views_persisted.csv",
            "path": ALL_PATHS["RESULTS"],
            "columns": ["entity", "space", "isPersisted", "runtime"],
        },
        "VIEW_UNPERSIST": {
            "name": "views_to_unpersist.csv",
            "path": ALL_PATHS["TASKS"],
            "columns": ["entity", "space"],
        },
        "VIEW_UNPERSIST_RESULT": {
            "name": "views_unpersisted.csv",
            "path": ALL_PATHS["RESULTS"],
            "columns": ["entity", "space", "isRemoved"],
        },
        "VIEW_PARTITION_LOCK": {
            "name": "views_to_lock_partitions.csv",
            "path": ALL_PATHS["TASKS"],
            "columns": ["entity", "space"],
        },
        "VIEW_PARTITION_LOCK_RESULT": {
            "name": "views_partitions_locked.csv",
            "path": ALL_PATHS["RESULTS"],
            "columns": ["entity", "space", "lockedPartitions"],
        },
        "VIEW_PARTITION_UNLOCK": {
            "name": "views_to_unlock_partitions.csv",
            "path": ALL_PATHS["TASKS"],
            "columns": ["entity", "space"],
        },
        "VIEW_PARTITION_UNLOCK_RESULT": {
            "name": "views_partitions_unlocked.csv",
            "path": ALL_PATHS["RESULTS"],
            "columns": ["entity", "space", "unlockedPartitions"],
        },
        "ANALYTICAL_MODELS_ALL_VIEWS": {
            "name": "analytical_models_with_all_views.json",
            "path": ALL_PATHS["EXPORTS"],
            "columns": None,
        },
        "ANALYTICAL_MODELS_ALL_VIEWS_IN_SPACE": {
            "name": "analytical_models_with_all_views_in_space.json",
            "path": ALL_PATHS["EXPORTS"],
            "columns": None,
        },
        "ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME": {
            "name": "analytical_models_to_check_view_persistence_time.csv",
            "path": ALL_PATHS["TASKS"],
            "columns": ["modelname", "space"],
        },
        "ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME_RESULT": {
            "name": "analytical_models_with_all_views_and_persistence_time.json",  # noqa: E501
            "path": ALL_PATHS["EXPORTS"],
            "columns": None,
        },
    }


class FileHandler:
    def __init__(self):
        # Variablen initialisieren
        self.quit_to_restart = False

        # Absolute Pfade setzen
        self.datasphere = Datasphere()

        # Alle Ordner erstellen
        self.set_absolute_path(self.datasphere.ALL_FILES)
        for directory in self.datasphere.ALL_PATHS.values():
            self.create_directory_if_not_exists(directory)

        # Alle Task-Dateien erstellen
        for file in filter(
            lambda file: file["path"] == self.datasphere.ALL_PATHS["TASKS"],
            self.datasphere.ALL_FILES.values(),
        ):
            self.create_file_if_not_exists(
                file["absolute_path"], file["columns"]
            )

        # Prüfen, ob neue Task-Dateien erstellt wurden und Programm neu
        # gestartet werden muss
        if self.quit_to_restart:
            logger.info(
                "Es wurden neue Dateien erstellt. "
                "Bitte Programm erneut starten... "
            )
            sys.exit()

        # Alle Export- und Result-Dateien erstellen / überschreiben
        # (außer .json Export-Dateien)
        for file in filter(
            lambda file: file["path"]
            in [
                self.datasphere.ALL_PATHS["EXPORTS"],
                self.datasphere.ALL_PATHS["RESULTS"],
            ],
            self.datasphere.ALL_FILES.values(),
        ):
            if "json" not in file["name"]:
                self.create_file(file["absolute_path"], file["columns"])

    def set_absolute_path(self, files: dict) -> None:
        for name, details in files.items():
            files[name]["absolute_path"] = os.path.join(
                PROJECT_PATH, details["path"], details["name"]
            )

    def create_directory_if_not_exists(self, path: str) -> None:
        directory_path = os.path.join(PROJECT_PATH, path)
        if not os.path.exists(directory_path):
            os.makedirs(directory_path)

    def create_file_if_not_exists(
        self, abs_path: str, columns: list[str] | None
    ) -> None:
        if not os.path.isfile(abs_path):
            self.quit_to_restart = True
            with open(abs_path, "w", encoding="utf-8") as file:
                if columns is not None:
                    writer = csv.DictWriter(file, fieldnames=columns)
                    writer.writeheader()

    def create_file(self, abs_path: str, columns: list[str] | None) -> None:
        with open(abs_path, "w", encoding="utf-8") as file:
            if columns is not None:
                file.write(",".join(columns) + "\n")
