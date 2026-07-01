from abc import abstractmethod
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

try:
    _APP_VERSION = f"v{pkg_version('sap-datasphere-automation')}"
except PackageNotFoundError:
    _APP_VERSION = "dev"

from datasphere.analytical_models import AnalyticalModels
from datasphere.remote_tables import RemoteTables
from datasphere.task_chains import TaskChains
from datasphere.views import Views
from static.logo import ASCII_LOGO

# Mapping of all menu categories, sub-categories and its options
type MenuOption = dict[str, Callable]
type SubCategory = dict[str, MenuOption]
MenuOptions: dict[str, MenuOption | SubCategory] = {
    "Analytical Models": {
        "Export all models with views": (
            AnalyticalModels.get_all_views_for_analytical_models
        ),
        "Export all models with views (by space)": (
            AnalyticalModels.get_all_views_for_analytical_models_in_space
        ),
        "Save runtime of all views of models": (
            AnalyticalModels.check_runtime_for_all_views_of_analytical_models
        ),
    },
    "Remote Tables": {
        "Create statistics for all tables": RemoteTables.create_statistics,
        "Refresh statistics for all tables": RemoteTables.refresh_statistics,
    },
    "Task Chains": {
        "Run task chains": TaskChains.run_task_chains,
    },
    "Views": {
        "Analytics": {
            "Export views with persistence score 10": (
                Views.create_view_analytics
            ),
            "Export views where attribute contains": (
                Views.get_all_views_where_attribute_contains
            ),
        },
        "Partitions": {
            "Create Partitions": Views.create_partitioning_for_views,
            "Remove Partitions": Views.remove_partitioning_for_views,
            "Lock Partitions until Year": Views.lock_partitions_until_year,
            "Unlock All Partitions": Views.unlock_all_partitions,
        },
        "Persistence": {
            "Persist Views": Views.persist_views,
            "Unpersist Views": Views.unpersist_views,
        },
    },
}



class BaseScreen(Screen):
    """
    BaseScreen class to inherit from. Creates the global header and footer for
    the CLI.

    Provides the method 'compose_content' which needs to be overridden by any
    inheriting classes.
    """

    def compose(self) -> ComposeResult:
        yield Static(id="header", content=ASCII_LOGO)
        yield from self.compose_content()
        yield Horizontal(
            Static("Ctrl+C to quit", id="footer-left"),
            Static(
                Text.from_markup(
                    "by [link=https://github.com/peterschwps]"
                    "@peterschwps[/link]"
                ),
                id="footer-center",
            ),
            Static(_APP_VERSION, id="footer-right"),
            id="footer",
        )

    @abstractmethod
    def compose_content(self) -> ComposeResult:
        raise NotImplementedError


class EntryScreen(BaseScreen):
    """
    Screen with the main menu. Shown after starting the CLI.
    """
    def __init__(self) -> None:
        super().__init__()

        # Holds all currently expanded menu options
        self._expanded: set[str] = set()

    def compose_content(self) -> ComposeResult:
        """
        Show menu in the main content widget.

        Yields:
            ComposeResult: Interactive menu widget.
        """
        yield Container(
            Static("\nSelect an option:"),
            OptionList(id="menu"),
            id="content",
        )

    def on_mount(self) -> None:
        """
        Event handler called after widget was added to the CLI.
        """
        self._rebuild_menu()

    def _rebuild_menu(self, restore_id: str | None = None) -> None:
        """
        Build the interactive menu.

        Args:
            restore_id (str | None, optional): ID of the cursor's position. If
                                               None the cursor will be set to 
                                               the first menu entry.
                                               Defaults to None.
        """
        # Fetch menu from DOM (only OptionList)
        menu = self.query_one(OptionList)

        # Clear menu and rebuild it
        menu.clear_options()
        for category, content in MenuOptions.items():
            
            # Display category
            is_expanded = category in self._expanded
            prefix = "▼ " if is_expanded else "▶ "
            menu.add_option(
                Option(
                    prompt=f"{prefix}{category}",
                    id=f"cat:{category}",
                )
            )

            # Show contents if expanded
            if is_expanded:
                for key, value in content.items():
                    
                    # Show sub-categories (only for "Views")
                    if isinstance(value, dict):
                        is_sub = f"{category}::{key}" in self._expanded
                        subprefix = "  ▼ " if is_sub else "  ▶ "
                        menu.add_option(
                            Option(
                                prompt=f"{subprefix}{key}",
                                id=f"subcat:{category}::{key}",
                            )
                        )

                        # Show options if sub-category expanded
                        if is_sub:
                            for action in value:
                                menu.add_option(
                                    Option(
                                        prompt=f"      {action}",
                                        id=f"act:{category}::{key}::{action}",
                                    )
                                )

                    # Show options
                    else:
                        menu.add_option(
                            Option(
                                prompt=f"    {key}",
                                id=f"act:{category}::{key}"
                            )
                        )

        # Set cursor back to previous position
        if restore_id:
            for i, opt in enumerate(menu._options):
                if opt.id == restore_id:
                    menu.highlighted = i
                    break
        else:
            # Set first menu entry to be highlighted
            # Otherwise the user would have to press an arrow key before any
            # menu option appears highlighted
            menu.highlighted = 0

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        """
        Event handler called when list option is selected.
        """
        # Get ID of current cursor position
        option_id = str(event.option.id)

        # Categories
        if option_id.startswith("cat:"):
            category = option_id[4:]
            if category in self._expanded:
                self._expanded = {
                    e for e in self._expanded
                    if not e.startswith(f"{category}::")
                }
                self._expanded.discard(category)
            else:
                self._expanded.add(category)
            self._rebuild_menu(restore_id=option_id)

        # Subcategories (only for "Views")
        elif option_id.startswith("subcat:"):
            path = option_id[7:]
            if path in self._expanded:
                self._expanded.discard(path)
            else:
                self._expanded.add(path)
            self._rebuild_menu(restore_id=option_id)
        
        # Options (that execute a method)
        elif option_id.startswith("act:"):
            parts = option_id[4:].split("::")
            if len(parts) == 2:
                category, action = parts
                self.app.exit(MenuOptions[category][action])
            elif len(parts) == 3:
                category, subcat, action = parts
                subcontent = cast(SubCategory, MenuOptions[category])
                self.app.exit(subcontent[subcat][action])


class DatasphereApp(App):
    """
    Global app configuration for the CLI. Calls the EntryScreen.
    """
    CSS_PATH = "static/test.tcss"
    MIN_WIDTH = 112
    BINDINGS = [Binding("ctrl+c", "quit", "Quit", show=False)]

    def on_mount(self) -> None:
        self.push_screen(EntryScreen())
