from utils.filehandler import file_setup
from utils.screens import DatasphereApp

# Create all files
# Must be executed first
file_setup()

# Load app
app = DatasphereApp()
app.run(mouse=False)
