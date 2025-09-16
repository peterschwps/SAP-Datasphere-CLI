import json
import os.path
import re
import sys
from random import randint
from time import sleep, time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from rich import get_console
from rich.prompt import Prompt
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.events import (
    AbstractEventListener,
    EventFiringWebDriver,
)
from selenium.webdriver.support.wait import WebDriverWait

from utils.filehandler import COOKIES_FILE, settings
from utils.logging import logger

# Wichtige Bedingungen aus Settings
URL_TO_USE: str = settings["Setup"]["URL_TO_USE"]
AUTHENTICATION_METHOD: str = settings["Setup"]["AUTHENTICATION_METHOD"]

# Wichtige URLs aus Settings
DATASPHERE_URL: str = settings["URLs"][URL_TO_USE]
SUBDOMAIN: str = urlparse(DATASPHERE_URL).hostname.split(".")[0]
SSO_URL: str = (
    f"https://{SUBDOMAIN}.authentication.eu10.hana.ondemand.com/saml/SSO/"
    f"alias/{SUBDOMAIN}.aws-live-eu10"
)
AUTH_URL: str = f"https://{SUBDOMAIN}.authentication.eu10.hana.ondemand.com"


class DatasphereAutomation:
    def __init__(self, session: requests.Session = None):
        # Requests Session initialisieren, falls noch nicht geschehen
        if session is not None:
            self.session = session
        else:
            self._initialize_requests_session()

    def _initialize_requests_session(self) -> None:
        """
        Initialisiert die Requests Session für alle weiteren Methoden.
        Nutzt die ausgewählte Authentifizierungsmethode per Requests oder
        Selenium.
        Lädt Cookies aus der COOKIES_FILE, falls diese existiert oder
        initialisiert eine erneute Anmeldung.
        """

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

            # Falls Cookies abgelaufen, Login starten
            if response.headers.get("X-Csrf-Token") is None:
                logger.debug("Gespeicherte Cookies sind abgelaufen.")

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

        else:
            logger.debug("Keine Cookies gefunden.")

        # Login starten
        if AUTHENTICATION_METHOD.upper() == "REQUESTS":
            logger.debug("Starte Microsoft SSO Login per Requests...")
            self._start_sso_login()

        elif AUTHENTICATION_METHOD.upper() == "BROWSER":
            logger.debug("Starte Login per Browser...")
            self._start_browser_login()
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

        else:
            logger.critical(
                "Ungültige Authentifizierungsmethode. "
                "Bitte in settings.env überprüfen."
            )
            sys.exit(1)

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
            logger.critical("Unbekannter Fehler. Bitte erneut starten...")
            if os.path.isfile(COOKIES_FILE):
                os.remove(COOKIES_FILE)
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

    def _start_sso_login(self) -> None:
        """
        Startet den SSO Login für Datasphere via Micrsoft.
        Sendet einen Code an die Authenticator App von Microsoft, falls
        erforderlich. Fragt dafür Username und Passwort per Prompt der Rich
        Console ab.
        Falls die Microsoft Login-Session weiterhin aktiv ist, wird der Login
        automatisch durchgeführt.
        """

        # Globale Rich Console speichern
        console = get_console()

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
        oauth_url = re.search(r'location="([^"]+)"', response.text).group(1)
        signature_cookie_value = re.search(
            r"signature=([^;]+)", response.text
        ).group(1)

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
        saml_url = f"{AUTH_URL}/" + soup.find("a")["href"]

        # 4. Request: https://<subdomain>.authentication.eu10.hana.ondemand.com/saml/discovery
        # Weiterleitung an: https://<subdomain>.authentication.eu10.hana.ondemand.com/saml/login/alias/<subdomain>.aws-live-eu10
        # erneute Weiterleitung an: https://login.microsoftonline.com/<tenant_id>/saml2
        self.session.headers.update({"Referer": f"{AUTH_URL}/login"})
        response = self.session.get(url=saml_url)

        # Prüfen, ob Bestätigung per MFA erforderlich
        # (nicht mehr im Hintergrund angemeldet)
        if "<title>Working...</title>" not in response.text:
            # Kurze Meldung und Zeit zum lesen
            logger.debug("Bestätigung per MFA erforderlich...\n\n")
            sleep(2)

            # Prompt für Username und Password via Rich
            prompt = Prompt()
            console.print(
                "Bitte E-Mail-Adresse zur Anmeldung via Microsoft SSO "
                "eingeben."
            )
            email = prompt.ask("\nE-Mail-Adresse")
            console.print("\nBitte Passwort des Microsoft Kontos eingeben.")
            console.print(
                "Achtung: Die Eingabe ist maskiert und wird deshalb nicht "
                "angezeigt.",
                style="bold yellow",
            )
            password = prompt.ask("\nPasswort", password=True)
            console.print("\nGeneriere MFA-Code...")

            # Config parsen
            config_data = re.search(r"\$Config=({.*?});", response.text).group(
                1
            )
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
            config_data = re.search(r"\$Config=({.*?});", response.text).group(
                1
            )
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
            end_auth_url = (
                "https://login.microsoftonline.com/common/SAS/EndAuth"
            )
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

        # Werte parsen
        soup = BeautifulSoup(response.text, "html.parser")
        saml_response = soup.find("input", attrs={"name": "SAMLResponse"})[
            "value"
        ]
        relay_state = soup.find("input", attrs={"name": "RelayState"})["value"]

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

    def _start_browser_login(self) -> None:
        """
        Konfiguriert den Selenium Browser und öffnet Edge zur manuellen
        Anmeldung.
        Lädt Cookies aus dem Browser und schließt ihn dann wieder.
        """

        # TODO: als Idee: edge://version/ bzw. chrome://version/ manuell öffnen
        #       und Profillink in Settings Datei kopieren
        #       dann Profil mit Selenium laden?

        # Event Listener um alle Cookies zu speichern (da get_cookies() sonst
        # nur Cookies von aktueller Seite abruft)
        # TODO: macht momentan keinen Sinn, weil Session nicht persistent ist,
        #       nur eine Stunde
        class CookieListener(AbstractEventListener):
            def __init__(self):
                self.all_cookies = []

            def after_navigate_to(self, url, driver):
                self.all_cookies.extend(driver.get_cookies())

        # WebDriver Einstellungen konfigurieren
        options = webdriver.EdgeOptions()
        options.add_argument("--log-level=CRITICAL")
        options.add_argument("start-maximized")
        options.add_argument("-inprivate")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])

        # TODO: Hier prüfen, ob Cookies noch funktionieren
        # Funktioniert aktuell nur so halb, nochmal mehr testen
        # Geht evtl. mit Playwright auch einfacher
        # Selenium kann nur Cookies von aktueller Website laden
        # bessere Idee: erst alle Cookies aus Datei filtern, jeweilige Domains einmal laden und dann alle Cookies reinladen  # noqa: E501
        # sonst lädt er aktuell die gleiche Seite 20 mal
        # with webdriver.Edge(options=options) as driver:

        #     # Cookies in Browser laden
        #     all_cookies = []
        #     with open(COOKIES_FILE, "r") as cookie_file:
        #         cookies = json.load(cookie_file)
        #         for cookie in cookies:
        #             if "login.microsoftonline.com" in cookie["domain"]:
        #                 driver.get(url=f"https://{cookie['domain'].strip('.')}")
        #                 driver.add_cookie({
        #                     "name": cookie["name"],
        #                     "value": cookie["value"],
        #                     "domain": cookie["domain"],
        #                     "path": cookie["path"],
        #                     "secure": cookie["secure"]
        #                 })

        #     # Wait initialisieren
        #     wait = WebDriverWait(driver, timeout=300)

        #     # Homepage laden
        #     driver.get(url=DATASPHERE_URL)
        #     loaded = wait.until(EC.text_to_be_present_in_element((By.ID, "__title0"), "SAP Datasphere"))  # noqa: E501

        #     # Aktualisierte Cookies überschreiben, neue Cookies hinzufügen
        #     if loaded:
        #         for cookie in driver.get_cookies():
        #             for num, stored_cookie in enumerate(all_cookies):
        #                 if cookie["name"] == stored_cookie["name"] and cookie["domain"] == stored_cookie["domain"]:  # noqa: E501
        #                     all_cookies[num] = cookie
        #                     break
        #             else:
        #                 all_cookies.append(cookie)
        #         return

        # WebDriver und WebDriverWait initialisieren
        with webdriver.Edge(options=options) as driver:
            # CookieListener und EventFiringWebDriver initialisieren
            listener = CookieListener()
            ef_driver = EventFiringWebDriver(driver, listener)

            # Wait initialisieren
            wait = WebDriverWait(ef_driver, timeout=300)

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

            # Cookies speichern
            logger.info("Speichere Cookies...")
            sleep(3)  # für zusätzliche Sicherheit
            listener.all_cookies.extend(
                ef_driver.get_cookies()
            )  # um Datasphere Cookies hinzuzufügen
            with open(COOKIES_FILE, "w") as cookie_file:
                json.dump(
                    listener.all_cookies,
                    cookie_file,
                    indent=4,
                    ensure_ascii=True,
                )

            # EventFiringWebDriver beenden
            ef_driver.quit()

    # TODO: Implementieren(?)
    # Damit werden alle Cookies gespeichert, auch die von Redirects.
    # Muss aber 'plawright install' manuell ausführen, um benötigte Browser
    # zu installieren.
    # Nochmal testen, ob es auch ohne funktioniert
    # Oder Pfad bei Kompilierung mit angeben, dann wird installierter Browser
    # mit geshippt wird.
    # TODO: und auch nochmal testen, wirkte so als ob Login doch noch nicht
    # länger als eine Stunde hält
    # ==> müsste eher initialize_requests_session() so anpassen, dass bei
    # Browser Login auch erstmal Cookies geladen werden.
    def _start_browser_login_playwright(self) -> None:
        """
        Konfiguriert den Playwright-Browser und öffnet Edge (oder Chromium) zur
        manuellen Anmeldung.
        Lädt Cookies aus dem Browser (alle Domains) und speichert sie dann.
        """
        from playwright.sync_api import sync_playwright

        # Playwright-Browser starten
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

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
            all_cookies = context.cookies()
            logger.info("Speichere Cookies...")
            with open(COOKIES_FILE, "w", encoding="utf-8") as cookie_file:
                json.dump(
                    all_cookies, cookie_file, indent=4, ensure_ascii=True
                )

            browser.close()
