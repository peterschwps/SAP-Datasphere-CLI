import json
import os.path
import re
import sys
from random import randint
from time import sleep, time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from rich import get_console
from rich.prompt import Prompt
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.abstract_event_listener import (
    AbstractEventListener,
)
from selenium.webdriver.support.event_firing_webdriver import (
    EventFiringWebDriver,
)
from selenium.webdriver.support.wait import WebDriverWait

from utils.filehandler import COOKIES_FILE, DATA_DIR, settings
from utils.logging import logger

# Wichtige Bedingungen aus Settings
URL_TO_USE: str = settings["Setup"]["URL_TO_USE"]
AUTHENTICATION_METHOD: str = settings["Setup"]["AUTHENTICATION_METHOD"]
BROWSER_TO_USE: str = settings["Setup"]["BROWSER_TO_USE"]

# Mapping of BROWSER_TO_USE to webdriver classes
BROWSER_MAPPING = {
    "CHROME": {
        "driver": webdriver.Chrome,
        "options": webdriver.ChromeOptions,
    },
    "EDGE": {
        "driver": webdriver.Edge,
        "options": webdriver.EdgeOptions,
    },
    "PLAYWRIGHT": None,
}

# Wichtige URLs aus Settings
DATASPHERE_URL: str = settings["URLs"][URL_TO_USE]
SUBDOMAIN: str = urlparse(DATASPHERE_URL).hostname.split(".")[0]  # pyright: ignore[reportOptionalMemberAccess]
SSO_URL: str = (
    f"https://{SUBDOMAIN}.authentication.eu10.hana.ondemand.com/saml/SSO/"
    f"alias/{SUBDOMAIN}.aws-live-eu10"
)
AUTH_URL: str = f"https://{SUBDOMAIN}.authentication.eu10.hana.ondemand.com"


class DatasphereAutomation:
    def __init__(self, session: requests.Session | None = None):
        """
        Initialisiert die Datasphere Automation, falls noch keine Session
        existiert.
        """
        if session is not None:
            self.session = session
        else:
            self._initialize_datasphere_session()

    def _initialize_datasphere_session(self) -> None:
        """
        Initialisiert die Datasphere Session für alle weiteren Methoden.
        Lädt Cookies aus der COOKIES_FILE, falls diese existiert oder
        initialisiert eine erneute Anmeldung.
        """

        # TODO: noch allgemeines Error Handling, dass Cookies löscht und
        # User benachrichtigt, dass er neustarten soll

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/"
                    "537.36 (KHTML, like Gecko) Chrome/138.0.0.0 "
                    "Safari/537.36 Edg/138.0.0.0"
                ),
                "Accept": "text/plain, */*; q=0.01",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "en",
                "Priority": "u=1, i",
                "X-Csrf-Token": "Fetch",
                "X-Requested-With": "XMLHttpRequest",
            }
        )

        # Cookies laden, falls Datei existiert
        if os.path.isfile(COOKIES_FILE):
            logger.info("Lade Cookies aus vorheriger Session...")
            # TODO: vielleicht nicht alle Cookies laden, sondern nur ESTAUTH
            with open(COOKIES_FILE) as cookie_file:
                cookies = json.load(cookie_file)
                for cookie in cookies:
                    self.session.cookies.set(
                        name=cookie["name"],
                        value=cookie["value"],
                        domain=cookie["domain"],
                        path=cookie["path"],
                        secure=cookie["secure"],
                    )

            # Prüfen, ob Cookie Session noch aktiv (1 Stunde)
            response = self.session.get(
                url=f"{DATASPHERE_URL}/sap/fpa/services/rest/epm/session",
                params={"action": "logon"},
            )

            # Falls Cookies abgelaufen sind
            if response.headers.get("X-Csrf-Token") is None:
                logger.debug(
                    "Datasphere Session ist abgelaufen. Starte neue Session..."
                )

            # Sonst Headers setzen und Initialisierung beenden
            else:
                self.session.headers.update(
                    {
                        "Accept-Language": "de",
                        "Origin": DATASPHERE_URL,
                        "X-Csrf-Token": response.headers["X-Csrf-Token"],
                    }
                )
                return

        # Falls keine Cookies gefunden wurden
        else:
            logger.debug("Keine Cookies gefunden.")

        # Authentifizierung/Refresh starten
        self._refresh_session()

        # Prüfen, ob Login erfolgreich
        self.session.headers.update(
            {
                "Accept": "text/plain, */*; q=0.01",
                "Accept-Language": "en",
                "Priority": "u=1, i",
                "X-Csrf-Token": "Fetch",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        response = self.session.get(
            url=f"{DATASPHERE_URL}/sap/fpa/services/rest/epm/session",
            params={"action": "logon"},
        )

        # Falls Anmeldung fehlgeschlagen (Cookies abgelaufen),
        # Browser erneut starten und Cookies laden
        if response.headers.get("X-Csrf-Token") is None:
            logger.critical("Unbekannter Fehler. Entferne Cookies...")
            if os.path.isfile(COOKIES_FILE):
                os.remove(COOKIES_FILE)
            logger.error("Bitte erneut starten...")
            sys.exit(1)

        # Headers setzen
        logger.info("Login erfolgreich.")
        self.session.headers.update(
            {
                "Accept-Language": "de",
                "Origin": DATASPHERE_URL,
                "X-Csrf-Token": response.headers["X-Csrf-Token"],
            }
        )

    def _start_login(
        self, response: requests.Response
    ) -> tuple[bool, requests.Response]:
        """
        Startet den Login per Requests oder Browser.
        Nutzt dafür den Wert aus der Settings-Datei.

        Lädt nach erfolgreicher Browser-Authentifizierung alle Cookies in die
        Requests Session.

        Returns:
            tuple[bool, requests.Response]: Bei Requests True als Indikator,
                                            dass der Flow weiter fortgeführt
                                            werden muss (damit persistente
                                            Auth-Cookies gespeichert werden.)
                                            Bei Browser False, um den weiteren
                                            Refresh-Session-Flow direkt zu
                                            beenden.
                                            Bei Requests wird die letzte
                                            Response zurückgegeben. Bei Browser
                                            wird derselbe Parameter
                                            zurückgegeben, der als Input
                                            übergeben wurde.
        """  # TODO: nochmal gucken, wie Docstring richtig formatieren, sieht komisch aus in Schnellübersicht  # noqa: E501
        if AUTHENTICATION_METHOD.upper() == "REQUESTS":
            logger.debug("Starte Microsoft SSO Login per Requests...")
            response = self._start_requests_authentication(response)
            return True, response

        elif AUTHENTICATION_METHOD.upper() == "BROWSER":
            if BROWSER_TO_USE.upper() == "PLAYWRIGHT":
                logger.debug("Starte Login per Playwright...")
                self._start_browser_login_playwright()
            else:
                logger.debug("Starte Login per Browser...")
                self._start_browser_authentication()
            with open(COOKIES_FILE) as cookie_file:
                cookies = json.load(cookie_file)
                for cookie in cookies:
                    self.session.cookies.set(
                        name=cookie["name"],
                        value=cookie["value"],
                        domain=cookie["domain"],
                        path=cookie["path"],
                        secure=cookie["secure"],
                    )
            return False, response

        else:
            logger.critical(
                "Ungültige Authentifizierungsmethode. "
                "Bitte in settings.env überprüfen."
            )
            sys.exit(1)

    def _refresh_session(self) -> None:
        """
        Aktualisiert die Datasphere Session mit den persistenten Auth-Cookies.
        Speichert die aktualisierten Cookies ab.

        Startet automatisch den vollen Authentifizierungsflow, falls die
        Auth-Cookies abgelaufen sind oder keine Cookies existieren.
        """

        # Wichtig: User-Agent muss Edge sein, sonst ist der Flow anders
        for header in ("Priority", "X-Csrf-Token", "X-Requested-With"):
            self.session.headers.pop(header)
        self.session.headers.update(
            {
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8,application/"
                    "signed-exchange;v=b3;q=0.7"
                ),
                "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )

        # 1. Request: https://<subdomain>.eu10.hcs.cloud.sap/dwaas-core/index.html
        response = self.session.get(
            url=f"{DATASPHERE_URL}/dwaas-core/index.html"
        )
        oauth_url_result = re.search(r'location="([^"]+)"', response.text)
        if not oauth_url_result:
            logger.critical("Fehler beim Parsen der OAuth URL.")
            sys.exit(1)
        oauth_url = oauth_url_result.group(1)

        signature_cookie_value_result = re.search(
            r"signature=([^;]+)", response.text
        )
        if not signature_cookie_value_result:
            logger.critical("Fehler beim Parsen des Signature Cookies.")
            sys.exit(1)
        signature_cookie_value = signature_cookie_value_result.group(1)

        # Cookies setzen, die via sonst Javascript werden
        # (wichtig für Redirect nach erfolgreicher Authentifizierung)
        datasphere_domain = urlparse(DATASPHERE_URL).hostname
        self.session.cookies.set(
            name="fragmentAfterLogin",
            value="#/home",
            domain=datasphere_domain,
            path="/",
            secure=True,
        )
        self.session.cookies.set(
            name="locationAfterLogin",
            value="/dwaas-core/index.html",
            domain=datasphere_domain,
            path="/",
            secure=True,
        )
        self.session.cookies.set(
            name="signature",
            value=signature_cookie_value,
            domain=datasphere_domain,
            path="/",
            secure=True,
        )

        # 2. Request: https://<subdomain>.authentication.eu10.hana.ondemand.com/oauth/authorize
        # mit Weiterleitung an: https://<subdomain>.authentication.eu10.hana.ondemand.com/login
        self.session.headers.update(
            {
                "DNT": "1",
                "Referer": f"{DATASPHERE_URL}/",
                "Accept-Language": "de",
            }
        )
        response = self.session.get(url=oauth_url)

        # SAML Link parsen
        soup = BeautifulSoup(response.text, "html.parser")
        saml_url = f"{AUTH_URL}/" + soup.find("a")["href"]  # type: ignore

        # 4. Request: https://<subdomain>.authentication.eu10.hana.ondemand.com/saml/discovery
        # Weiterleitung an: https://<subdomain>.authentication.eu10.hana.ondemand.com/saml/login/alias/<subdomain>.aws-live-eu10
        # erneute Weiterleitung an: https://login.microsoftonline.com/<tenant_id>/saml2
        self.session.headers.update({"Referer": f"{AUTH_URL}/login"})
        response = self.session.get(url=saml_url)

        # Prüfen, ob Bestätigung per MFA erforderlich
        # (nicht mehr im Hintergrund angemeldet)
        # --> Login starten
        if "<title>Working...</title>" not in response.text:
            proceed, response = self._start_login(response)

            # Falls Browser-Login durchgeführt wurde
            if not proceed:
                return

        # Werte parsen
        soup = BeautifulSoup(response.text, "html.parser")
        saml_response = soup.find("input", attrs={"name": "SAMLResponse"})[  # type: ignore
            "value"
        ]
        relay_state = soup.find("input", attrs={"name": "RelayState"})["value"]  # type: ignore

        # 5. / 11. Request: https://<subdomain>.authentication.eu10.hana.ondemand.com/saml/SSO/alias/<subdomain>.aws-live-eu10
        # Weiterleitung an: https://<subdomain>.authentication.eu10.hana.ondemand.com/oauth/authorize
        # Weiterleitung an: https://<subdomain>.eu10.hcs.cloud.sap/sso/login/callback
        data = {"SAMLResponse": saml_response, "RelayState": relay_state}
        self.session.headers.update(
            {"Referer": "https://login.microsoftonline.com/"}
        )
        response = self.session.post(url=SSO_URL, data=data)

        # Cookies in Datei speichern
        logger.info("Speichere Cookies...")
        with open(COOKIES_FILE, "w") as cookies_file:
            json.dump(
                [
                    {
                        "domain": cookie.domain,
                        "name": cookie.name,
                        "path": cookie.path,
                        "secure": cookie.secure,
                        "value": cookie.value,
                    }
                    for cookie in self.session.cookies
                ],
                cookies_file,
            )

        # Header vorbereiten auf Methodenaufrufe
        for header in (
            "Connection",
            "Upgrade-Insecure-Requests",
            "DNT",
            "Referer",
        ):
            self.session.headers.pop(header)

    def _start_requests_authentication(
        self, response: requests.Response
    ) -> requests.Response:
        """
        Startet den Microsoft SSO Login per Requests.
        Sendet einen Code an die Authenticator App von Microsoft. Fragt dafür
        Username und Passwort per Prompt der Rich Console ab.

        Args:
            response (requests.Response): Reponse der letzten vorherigen
                                          Anfrage (Konfiguration für MFA).

        Returns:
            requests.Response: Response der letzten Anfrage (Auth-Verarbeitung
                               nach erfolgreicher MFA).
        """

        # Globale Rich Console speichern
        console = get_console()

        # Kurze Meldung und Zeit zum lesen
        logger.debug("Bestätigung per MFA erforderlich...\n\n")
        sleep(2)

        # Prompt für Username und Password via Rich
        prompt = Prompt()
        console.print(
            "Bitte E-Mail-Adresse zur Anmeldung via Microsoft SSO eingeben."
        )
        email = prompt.ask("\nE-Mail-Adresse").strip()
        console.print("\nBitte Passwort des Microsoft Kontos eingeben.")
        console.print(
            "Achtung: Die Eingabe ist maskiert und wird deshalb nicht "
            "angezeigt.",
            style="bold yellow",
        )
        password = prompt.ask("\nPasswort", password=True)
        console.print("\nGeneriere MFA-Code...")

        # Config parsen
        config_data_result = re.search(r"\$Config=({.*?});", response.text)
        if not config_data_result:
            logger.critical("Fehler beim Parsen der OAuth Config.")
            sys.exit(1)
        config_data = config_data_result.group(1)
        config_data = json.loads(config_data)
        correlation_id = config_data["correlationId"]

        # 5. Request: https://login.microsoftonline.com/common/GetCredentialType?mkt=de-DE
        # TODO: Params parsen?
        url = "https://login.microsoftonline.com/common/GetCredentialType?mkt=de-DE"
        data = {
            "username": email,
            "isOtherIdpSupported": True,
            "checkPhones": False,
            "isRemoteNGCSupported": True,
            "isCookieBannerShown": False,
            "isFidoSupported": True,
            "originalRequest": config_data["sCtx"],
            "country": "DE",
            "forceotclogin": False,
            "isExternalFederationDisallowed": False,
            "isRemoteConnectSupported": False,
            "federationFlags": 0,
            "isSignup": False,
            "flowToken": config_data["sFT"],
            "isAccessPassSupported": True,
            "isQrCodePinSupported": True,
        }
        self.session.headers.update(
            {
                "hpgid": str(config_data["hpgid"]),
                "hpgact": str(config_data["hpgact"]),
                "canary": config_data["apiCanary"],
                "client-request-id": correlation_id,
                "Accept": "application/json",
                "hpgrequestid": config_data["sessionId"],
                "DNT": "1",
                "Origin": "https://login.microsoftonline.com",
                "Referer": response.url,
            }
        )
        response = self.session.post(url=url, json=data)
        flow_token = response.json()["FlowToken"]

        # Headers wieder entfernen
        for header in (
            "hpgid",
            "hpgact",
            "canary",
            "client-request-id",
            "hpgrequestid",
        ):
            self.session.headers.pop(header)

        # 6. Request: https://login.microsoftonline.com/<tenant_id>/login
        login_url = config_data["urlPost"]
        i19 = randint(12000, 20000)
        data = {
            "i13": 0,
            "login": email,
            "loginfmt": email,
            "type": 11,
            "LoginOptions": 3,
            "lrt": "",
            "lrtPartition": "",
            "hisRegion": "",
            "hisScaleUnit": "",
            "passwd": password,
            "ps": 2,
            "psRNGCDefaultType": "",
            "psRNGCEntropy": "",
            "psRNGCSLK": "",
            "canary": config_data["canary"],
            "ctx": config_data["sCtx"],
            "hpgrequestid": config_data["sessionId"],
            "flowToken": flow_token,
            "PPSX": "",
            "NewUser": 1,
            "FoundMSAs": "",
            "fspost": 0,
            "i21": 0,
            "CookieDisclosure": 0,
            "IsFidoSupported": 1,
            "isSignupPost": 0,
            "DfpArtifact": "",
            "i19": i19,
        }
        self.session.headers.update(
            {
                "Origin": "https://login.microsoftonline.com",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
            }
        )
        response = self.session.post(url=login_url, data=data)

        # Neue Config parsen
        config_data_result = re.search(r"\$Config=({.*?});", response.text)
        if not config_data_result:
            logger.critical("Fehler beim Parsen der OAuth Config.")
            sys.exit(1)
        config_data = config_data_result.group(1)
        config_data = json.loads(config_data)

        # 7. Request: https://login.microsoftonline.com/common/SAS/BeginAuth
        begin_auth_url = (
            "https://login.microsoftonline.com/common/SAS/BeginAuth"
        )
        data = {
            "AuthMethodId": "PhoneAppNotification",
            "Method": "BeginAuth",
            "ctx": config_data["sCtx"],
            "flowToken": config_data["sFT"],
        }
        self.session.headers.update(
            {
                "hpgid": str(config_data["hpgid"]),
                "hpgact": str(config_data["hpgact"]),
                "canary": config_data["apiCanary"],
                "client-request-id": config_data["correlationId"],
                "Accept": "application/json",
                "hpgrequestid": config_data["sessionId"],
                "Origin": "https://login.microsoftonline.com",
                "Referer": response.url,
            }
        )
        response = self.session.post(url=begin_auth_url, json=data)
        auth_data = response.json()

        # Authenticator Code ausgeben
        if str(auth_data["Entropy"]) == 0:
            console.print(
                "Unbekannter Fehler. Bitte erneut starten.",
                style="bold red",
            )
            sys.exit()
        entropy = auth_data["Entropy"]
        console.print(f"Authenticator Code: {entropy}", style="bold green")

        # 8. Request: https://login.microsoftonline.com/common/SAS/EndAuth
        end_auth_url = "https://login.microsoftonline.com/common/SAS/EndAuth"
        poll_count = 1
        params = {
            "authMethodId": "PhoneAppNotification",
            "pollCount": poll_count,
        }
        self.session.headers.update(
            {
                "x-ms-flowToken": auth_data["FlowToken"],
                "x-ms-ctx": auth_data["Ctx"],
                "client-request-id": auth_data["CorrelationId"],
                "x-ms-sessionId": auth_data["SessionId"],
            }
        )

        # 9. Request (und weitere Requests) um Stand der MFA zu prüfen
        console.print("\nWarte auf Bestätigung...")
        last_poll_start_time = None
        last_poll_end_time = None
        flow_token = None
        ctx = None
        while True:
            last_poll_start_time = round(time() * 1000)
            challenge_data = self.session.get(
                url=end_auth_url, params=params
            ).json()
            last_poll_end_time = round(time() * 1000)
            if challenge_data["ResultValue"] != "AuthenticationPending":
                flow_token = challenge_data["FlowToken"]
                ctx = challenge_data["Ctx"]
                break
            params["lastPollStart"] = last_poll_start_time
            params["lastPollEnd"] = last_poll_end_time
            sleep(1)
        console.print("Bestätigung erhalten.\n\n", style="bold green")

        # 10. Request: https://login.microsoftonline.com/common/SAS/ProcessAuth
        process_auth_url = (
            "https://login.microsoftonline.com/common/SAS/ProcessAuth"
        )
        data = {
            "type": 22,
            "request": ctx,
            "mfaLastPollStart": last_poll_start_time,
            "mfaLastPollEnd": last_poll_end_time,
            "mfaAuthMethod": "PhoneAppNotification",
            "login": email,
            "flowToken": flow_token,
            "hpgrequestid": response.headers["x-ms-request-id"],
            "sacxt": "",
            "hideSmsInMfaProofs": False,
            "canary": config_data["canary"],
            "i19": i19 + randint(500, 1000),
        }
        for headers in (
            "hpgid",
            "hpgact",
            "canary",
            "client-request-id",
            "hpgrequestid",
            "x-ms-flowToken",
            "x-ms-ctx",
            "x-ms-sessionId",
        ):
            self.session.headers.pop(headers)
        self.session.headers.update(
            {
                "Origin": "https://login.microsoftonline.com",
                "Upgrade-Insecure-Requests": "1",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
            }
        )
        response = self.session.post(url=process_auth_url, data=data)
        self.session.headers.pop("Origin")
        return response

    def _start_browser_authentication(self) -> None:
        """
        Konfiguriert den Selenium Browser zur manuellen Anmeldung.
        Lädt Cookies aus dem Browser und schließt ihn dann wieder.
        """

        # TODO: Edge noch testen!

        # Event Listener um alle Cookies zu speichern (da get_cookies() sonst
        # nur Cookies von aktueller Seite abruft)
        class CookieListener(AbstractEventListener):
            def __init__(self):
                self.all_cookies = []

            def after_navigate_to(self, _, driver):
                """
                Wird nach jeder Navigation aufgerufen.
                ABER: Die Cookies der geladenen Seite müssen explizit
                abgespeichert werden.

                Args:
                    _ (Any): Unused arguments.
                    driver (WebDriver): WebDriver to add listener to.
                """
                self.all_cookies.extend(driver.get_cookies())

        # WebDriver Einstellungen konfigurieren
        options = BROWSER_MAPPING[BROWSER_TO_USE]["options"]()
        options.add_argument("--log-level=CRITICAL")
        options.add_argument("start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])

        # Pfad für Browser Profile erstellen/setzen
        browser_data_path = DATA_DIR / f".{BROWSER_TO_USE.lower()}"
        if not os.path.exists(browser_data_path):
            os.mkdir(browser_data_path)
        options.add_argument(f"user-data-dir={browser_data_path}")

        # WebDriver und WebDriverWait initialisieren
        with BROWSER_MAPPING[BROWSER_TO_USE]["driver"](
            options=options
        ) as driver:
            # CookieListener und EventFiringWebDriver initialisieren
            listener = CookieListener()
            ef_driver = EventFiringWebDriver(driver, listener)

            # Wait initialisieren
            wait = WebDriverWait(
                ef_driver,  # pyright: ignore[reportArgumentType]
                timeout=300,
            )

            # Homepage laden
            logger.debug(
                "Lade Datasphere Homepage im Browser. "
                "Bitte manuell anmelden..."
            )
            ef_driver.get(url=DATASPHERE_URL)
            wait.until(
                EC.text_to_be_present_in_element(
                    (By.ID, "__title0"), "SAP Datasphere"
                )
            )
            listener.all_cookies.extend(
                ef_driver.get_cookies()
            )  # WICHTIG: um Datasphere Cookies hinzuzufügen
            sleep(5)

            # WICHTIG: um persistente Microsoft Auth-Cookies zu erhalten
            logger.debug("Lade Microsoft Login...")
            ef_driver.get(url="https://login.microsoftonline.com")
            sleep(5)  # für zusätzliche Sicherheit

            # Cookies speichern
            logger.info("Speichere Cookies...")
            with open(COOKIES_FILE, "w") as cookie_file:
                json.dump(
                    listener.all_cookies,
                    cookie_file,
                    indent=4,
                    ensure_ascii=True,
                )

            # EventFiringWebDriver beenden
            ef_driver.quit()

    # TODO: In Guide: 'playwright install' muss manuell ausgeführt werden
    def _start_browser_login_playwright(self) -> None:
        """
        Konfiguriert den Playwright-Browser zur manuellen Anmeldung.
        Lädt Cookies aus dem Browser und schließt ihn dann wieder.

        Speichert automatisch alle Cookies der Session.
        """

        # Pfad für Browser Profile erstellen/setzen
        browser_data_path = DATA_DIR / f".{BROWSER_TO_USE.lower()}"
        if not os.path.exists(browser_data_path):
            os.mkdir(browser_data_path)

        # Playwright-Browser starten
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=browser_data_path,
                headless=False,
                args=["--start-maximized"],
                no_viewport=True,
            )
            page = browser.new_page()

            # Seite öffnen und auf Login warten
            logger.debug(
                "Lade Datasphere Homepage im Browser. "
                "Bitte manuell anmelden..."
            )
            page.goto(DATASPHERE_URL)
            page.wait_for_selector(
                "#__title0", timeout=300000
            )  # 5 Minuten Timeout

            # Kurze Wartezeit für zusätzliche Sicherheit
            sleep(3)

            # Alle Cookies aus allen Domains sammeln
            all_cookies = browser.cookies()
            logger.info("Speichere Cookies...")
            with open(COOKIES_FILE, "w", encoding="utf-8") as cookie_file:
                json.dump(
                    all_cookies, cookie_file, indent=4, ensure_ascii=True
                )

            # Browser beenden
            browser.close()
