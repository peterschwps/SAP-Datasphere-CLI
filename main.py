import asyncio
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
    # Neue Instanz der ausgewählten Klasse erzeugen
    app = chosen_module()

    async def run_task():
        # Initialisierungsmethode der Klasse ausführen
        # (erstellt Datasphere Session)
        await app.initialize()

        # Ungebundene Methode zurückgeben (erhält Instanz als erstes Argument)
        return await chosen_method(app, **all_params)

    result = asyncio.run(run_task())
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

# Weitere TODO:
# - Idee: Automatisierung von View-Transporten?
# - Updates von Headern besser organisieren? Dass immer volle Headers gesetzt
#   werden?
