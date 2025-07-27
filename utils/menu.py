import sys

from datasphere.helper import AnalyticalModels, RemoteTables, Views
from time import sleep
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.text import Text


# Alle Error-Nachrichten überschreiben
class CustomizedPrompt(Prompt):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    # Error-Nachricht überschreiben
    def on_validate_error(self, value: str, *args, **kwargs):
        self.console.print("Bitte einen gültigen Wert eingeben.", style="bold red")


class CustomizedIntPrompt(IntPrompt):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    # Error-Nachricht überschreiben
    def on_validate_error(self, value: str, *args, **kwargs):
        self.console.print("Bitte eine gültige Nummer eingeben.", style="bold red")


class CustomizedConfirmPrompt(Confirm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    # Error-Nachricht überschreiben
    def on_validate_error(self, value: str, *args, **kwargs):
        self.console.print("Bitte nur 'y' oder 'n' eingeben.\n", style="bold red", highlight=False)


class Menu:

    def __init__(self):

        # Mapping der Klassen
        self.classes = {
            "Analytische Modelle": AnalyticalModels,
            "Remote Tabellen": RemoteTables,
            "Views": Views
        }

        # Mapping der Funktionen für die Menüs
        self.modules = {
            "Analytische Modelle": {
                "Alle Modelle inkl. Views speichern": AnalyticalModels.get_all_views_for_analytical_models,
                "Alle Modelle in einem bestimmten Space inkl. Views speichern": AnalyticalModels.get_all_views_for_analytical_models_in_space,
                "Laufzeiten aller Views von Modellen speichern": AnalyticalModels.check_runtime_for_all_views_of_analytical_models,
            },
            "Remote Tabellen": {
                "Statistiken für alle Tabellen erstellen": RemoteTables.create_statistics,
                "Statistiken für alle Tabellen aktualisieren": RemoteTables.refresh_statistics
            },
            "Views": {
                "Alle Views mit Persistenz-Score 10 speichern": Views.create_view_analytics,
                "Alle Views abspeichern, die Attribut mit Substring haben": Views.get_all_views_where_attribute_contains,
                "Partitionen für Views erstellen": Views.create_partitioning_for_views,
                "Partitionen für Views entfernen": Views.remove_partitioning_for_views,
                "Partitionen für Views bis zu einem bestimmten Jahr sperren": Views.lock_partitions_until_year,
                "Partitionen für Views entsperren": Views.unlock_all_partitions,
                "Views persistieren": Views.persist_views,
                "Views 'entpersistieren'": Views.unpersist_views
            }
        }

        # Konsole und Prompts initialisieren
        self.console = Console()
        self.prompt = CustomizedPrompt(console=self.console, show_choices=False)
        self.int_prompt = CustomizedIntPrompt(console=self.console, show_choices=False)
        self.confirm_prompt = CustomizedConfirmPrompt(console=self.console)

        # Konsole bereinigen
        self.console.clear()

        # Variablen initialisieren
        self.chosen_class = None
        self.chosen_method = None
        self.all_params = {}

    # Funktion um Menü anzuzeigen
    def show_menu(self, options: dict = None, is_first_menu: bool = True) -> tuple[Callable, Callable, dict]:
        """
        Zeigt ein Menü mit einer Überschrift, einem Subtitel und einer Liste von Modulen an.
        Gibt die ausgewählte Option als Integer zurück.

        Args:
            options (dict, optional): Dictionary mit den Klassen bzw. ihren Methoden.
            is_first_menu (bool, optional): True, wenn es das erste Menü ist (dann "Beenden" Option, sonst "Zurück").
                                            Standard ist False.

        Returns:
            int: Ausgewählte Option als Integer.
        """

        # Falls keine Module übergeben, das erste Menü anzeigen
        if options is None:
            options = self.classes

        # Header für alle Menüs
        self.console.print(Panel(Text("SAP Automation", style="bold", justify="center")))

        # Subtitle anzeigen
        self.console.print(Text(f"\n {'Kategorien' if is_first_menu else 'Verfügbare Skripte'}\n", style="bold"))

        # Alle Optionen (Kategorien/Skripte) anzeigen
        for index, option in enumerate(options.keys()):
            self.console.print(f" {index+1}. {option}", highlight=False)

        # Beenden- / Zurück-Option hinzufügen
        self.console.print(Text(f" 0. {'Beenden' if is_first_menu else 'Zurück'}", style="grey42"))

        # User Input abfragen
        chosen_option = self.prompt.ask("\nNummer eingeben", show_choices=False,
                                choices=[str(num) for num in range(len(options)+1)])
        chosen_option_int = int(chosen_option)
        self.console.clear()

        # Prüfen ob Beenden
        if chosen_option_int == 0:
            if is_first_menu:
                self.console.print("Beende Programm...")
                for _ in range(5):
                    self.console.print(".", style="bold")
                    sleep(1/5)
                self.console.clear()
                sys.exit()
            else:
                return self.show_menu(is_first_menu=True)
            
        # Andernfalls Menü der jeweiligen Kategorie aufrufen oder gewähltes Modul zurückgeben
        else:
            if is_first_menu:
                self.chosen_class = self.classes[list(options.keys())[chosen_option_int-1]]
                return self.show_menu(options=self.modules[list(options.keys())[chosen_option_int-1]],
                                      is_first_menu=False)
            else:
                self.chosen_method = options[list(options.keys())[chosen_option_int-1]]
                self.set_params_for_method()
                return self.chosen_class, self.chosen_method, self.all_params
            
    def set_params_for_method(self) -> None:
        """
        Setzt die Parameter für die gewählte Methode.
        Bereinigt anschliessend die Konsole.
        """

        # Funktionen für ähnliche Abfragen
        def ask_for_threads() -> None:
            use_threads = self.confirm_prompt.ask("Threads nutzen", case_sensitive=False)
            if use_threads:
                thread_count = self.int_prompt.ask("Anzahl der Threads")
                self.all_params["use_threads"] = use_threads
                self.all_params["thread_count"] = thread_count
            else:
                self.all_params["use_threads"] = use_threads

        # Gewählte Klasse und Methode filtern
        # Analytische Modelle
        if self.chosen_method in AnalyticalModels.__dict__.values():
            if self.chosen_method == AnalyticalModels.get_all_views_for_analytical_models:
                self.all_params["skip_duplicates"] = self.confirm_prompt.ask("Duplikate überspringen",
                                                                             case_sensitive=False)
                
            elif self.chosen_method == AnalyticalModels.get_all_views_for_analytical_models_in_space:
                self.all_params["space_name"] = self.prompt.ask("Name des Spaces (z.B. CENTRAL_IT)")
                self.all_params["skip_duplicates"] = self.confirm_prompt.ask("Duplikate überspringen",
                                                                             case_sensitive=False)
                
            elif self.chosen_method == AnalyticalModels.check_runtime_for_all_views_of_analytical_models:
                ask_for_threads()

        # RemoteTables
        elif self.chosen_method in RemoteTables.__dict__.values():

            if self.chosen_method == RemoteTables.create_statistics:
                self.console.print("Statistiktyp auswählen:")
                self.console.print("1. Anzahl der Datensätze")
                self.console.print("2. Einfache Statistik")
                self.console.print("3. Histogram")
                types = ["RECORD_COUNT", "SIMPLE", "HISTOGRAM"]
                index = self.int_prompt.ask("\nNummer eingeben", choices=["1", "2", "3"], show_choices=False)
                type = types[index-1]
                self.all_params["type"] = type
                
            elif self.chosen_method == RemoteTables.refresh_statistics:
                ask_for_threads()

        # Views
        elif self.chosen_method in Views.__dict__.values():

            if self.chosen_method == Views.get_all_views_where_attribute_contains:
                self.all_params["word"] = self.prompt.ask("Suchwort")
            
            elif self.chosen_method == Views.create_view_analytics:
                ask_for_threads()

            elif self.chosen_method == Views.create_partitioning_for_views:
                self.console.print("Bitte den Bereich der Partitionen eingeben.")
                self.console.print("Partitionen werden in jährlichen Intervallen erstellt.\n")
                partition_start = self.int_prompt.ask("Untergrenze der ersten Partition (>=):")
                partition_end = self.int_prompt.ask("Obergrenze der letzten Partition (<):")
                self.all_params["partitions"] = [str(year) for year in range(partition_start, partition_end+1)]
                self.all_params["overwrite_existing_partitions"] = self.confirm_prompt.ask(
                    "\nBestehende Partitionen überschreiben",
                    case_sensitive=False
                )

            elif self.chosen_method == Views.persist_views:
                ask_for_threads()
                self.all_params["timer"] = self.confirm_prompt.ask("Laufzeit speichern", case_sensitive=False)
                
            elif self.chosen_method == Views.unpersist_views:
                ask_for_threads()

            elif self.chosen_method == Views.lock_partitions_until_year:
                self.console.print("Bitte das Jahr eingeben, bis zu dem die Partitionen gesperrt werden sollen.")
                self.console.print("Das eingegebene Jahr wird wir auch noch gesperrt.\n")
                self.all_params["year"] = self.int_prompt.ask("Jahr")
                
        # Konsole bereinigen
        self.console.clear()
