### PyInstaller Befehl
```bash
pyinstaller --onefile main.py --name DatasphereAutomation --icon datasphere.ico
```
<br>

### Installation für Entwickler
Voraussetzung: <a href="https://python-poetry.org/docs/#installation">Poetry</a> ist installiert
```bash
poetry install --no-root
```
Ausführen per:
```bash
poetry run python main.py
```