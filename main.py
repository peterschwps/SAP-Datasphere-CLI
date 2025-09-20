import sys

from utils.filehandler import file_setup
from utils.logging import logger
from utils.menu import Menu

# Alle Dateien erstellen
# Muss IMMER zuerst ausgeführt werden
file_setup()

# Menü starten
menu = Menu()
chosen_module, chosen_method, all_params = menu.show_menu()

# Tasks starten
exit_code = 1
try:
    app = chosen_module()  # neue Instanz der gewünschten Klasse erzeugen
    result = chosen_method(
        app, **all_params
    )  # ungebundene Methode, die als erstes Argument die Instanz erhält
    exit_code = 0

except KeyboardInterrupt:
    exit_code = 130

logger.warning(
    "Bitte beachten, dass beim nächsten Start alle Dateien im Exports- und "
    "Results-Ordner überschrieben werden!"
)
logger.info("Programm wird beendet...")
sys.exit(exit_code)

# TODO: noch letztes bisschen Logging von Selenium auf Windows wegbekommen
# TODO: Funktion best Views per View Analyzer mit allen analytischen Modellen
#       machen
# TODO: Asyncio statt Threads?

# Weitere TODO:
# - Idee: Automatisierung von View-Transporten?
# - Updates von Headern besser organisieren? Dass immer volle Headers gesetzt
#   werden?
