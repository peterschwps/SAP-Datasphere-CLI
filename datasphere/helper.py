import concurrent.futures
import csv
import json
import os.path
import threading
import re
import sys

from copy import deepcopy
from datasphere.custom_types import StatisticsType, StatisticsDict, ViewDetailsDict, AnalyticalModelsDetailsDict
from datetime import datetime
from dateutil import tz
from random import randint
from time import sleep, time
from typing import Optional
from urllib.parse import quote, urlencode, urlparse
from utils.filehandler import settings, Datasphere, COOKIES_FILE
from utils.logging import logger
from uuid import uuid4

import pandas as pd
import requests

from bs4 import BeautifulSoup
from rich import get_console
from rich.prompt import Prompt
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support.events import EventFiringWebDriver, AbstractEventListener


# Wichtige Bedingungen aus Settings
URL_TO_USE: str = settings["Setup"]["URL_TO_USE"]
AUTHENTICATION_METHOD: str = settings["Setup"]["AUTHENTICATION_METHOD"]

# Wichtige URLs aus Settings
DATASPHERE_URL: str = settings["URLs"][URL_TO_USE]
SUBDOMAIN: str = urlparse(DATASPHERE_URL).hostname.split(".")[0]
SSO_URL: str = f"https://{SUBDOMAIN}.authentication.eu10.hana.ondemand.com/saml/SSO/alias/{SUBDOMAIN}.aws-live-eu10"
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
        Nutzt die ausgewählte Authentifizierungsmethode per Requests oder Selenium.
        Lädt Cookies aus der COOKIES_FILE, falls diese existiert oder initialisiert eine erneute Anmeldung.
        """

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " \
                            "Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0",
            "Accept": "text/plain, */*; q=0.01",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en",
            "Priority": "u=1, i",
            "X-Csrf-Token": "Fetch",
            "X-Requested-With": "XMLHttpRequest",
        })

        # Cookies laden, falls Datei existiert
        if os.path.isfile(COOKIES_FILE):
            logger.info("Lade Cookies aus vorheriger Session...")
            with open(COOKIES_FILE, "r") as cookie_file:
                cookies = json.load(cookie_file)
                for cookie in cookies:
                    self.session.cookies.set(
                        name=cookie["name"],
                        value=cookie["value"],
                        domain=cookie["domain"],
                        path=cookie["path"],
                        secure=cookie["secure"]
                    )

            # Prüfen, ob Cookie Session noch aktiv (1 Stunde)
            response = self.session.get(url=f"{DATASPHERE_URL}/sap/fpa/services/rest/epm/session",
                                        params={"action": "logon"})
            
            # Falls Cookies abgelaufen, Login starten
            if response.headers.get("X-Csrf-Token") is None:
                logger.debug("Gespeicherte Cookies sind abgelaufen.")

            # Sonst Headers setzen und Initialisierung beenden
            else:
                self.session.headers.update({
                    "Accept-Language": "de",
                    "Origin": DATASPHERE_URL,
                    "X-Csrf-Token": response.headers["X-Csrf-Token"]
                })
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
            with open(COOKIES_FILE, "r") as cookie_file:
                cookies = json.load(cookie_file)
                for cookie in cookies:
                    self.session.cookies.set(
                        name=cookie["name"],
                        value=cookie["value"],
                        domain=cookie["domain"],
                        path=cookie["path"],
                        secure=cookie["secure"]
                    )

        else:
            logger.critical("Ungültige Authentifizierungsmethode. Bitte in settings.env überprüfen.")
            sys.exit(1)

        # Prüfen, ob Login erfolgreich
        self.session.headers.update({
            "Accept": "text/plain, */*; q=0.01",
            "Accept-Language": "en",
            "Priority": "u=1, i",
            "X-Csrf-Token": "Fetch",
            "X-Requested-With": "XMLHttpRequest",
        })
        response = self.session.get(url=f"{DATASPHERE_URL}/sap/fpa/services/rest/epm/session",
                                    params={"action": "logon"})
        
        # Falls Anmeldung fehlgeschlagen (Cookies abgelaufen), Browser erneut starten und Cookies laden
        if response.headers.get("X-Csrf-Token") is None:
            logger.critical("Unbekannter Fehler. Bitte erneut starten...")
            if os.path.isfile(COOKIES_FILE):
                os.remove(COOKIES_FILE)
            sys.exit(1)

        # Headers setzen
        logger.info("Login erfolgreich.")
        self.session.headers.update({
            "Accept-Language": "de",
            "Origin": DATASPHERE_URL,
            "X-Csrf-Token": response.headers["X-Csrf-Token"]
        })

    def _start_sso_login(self) -> None:
        """
        Startet den SSO Login für Datasphere via Micrsoft.
        Sendet einen Code an die Authenticator App von Microsoft, falls erforderlich. Fragt dafür Username und Passwort
        per Prompt der Rich Console ab.
        Falls die Microsoft Login-Session weiterhin aktiv ist, wird der Login automatisch durchgeführt.
        """

        # Globale Rich Console speichern
        console = get_console()
        
        # Wichtig: User-Agent muss Edge sein, sonst ist der Flow anders
        for header in ("Priority", "X-Csrf-Token", "X-Requested-With"):
            self.session.headers.pop(header)
        self.session.headers.update({
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;" \
                      "q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"
        })

        # 1. Request: https://<subdomain>.eu10.hcs.cloud.sap/dwaas-core/index.html
        response = self.session.get(url=f"{DATASPHERE_URL}/dwaas-core/index.html")
        oauth_url = re.search(r'location="([^"]+)"', response.text).group(1)
        signature_cookie_value = re.search(r'signature=([^;]+)', response.text).group(1)

        # Cookies setzen, die via sonst Javascript werden (wichtig für Redirect nach erfolgreicher Authentifizierung)
        datasphere_domain = urlparse(DATASPHERE_URL).hostname
        self.session.cookies.set(name="fragmentAfterLogin", value="#/home", domain=datasphere_domain,
                                 path="/", secure=True)
        self.session.cookies.set(name="locationAfterLogin", value="/dwaas-core/index.html",
                                 domain=datasphere_domain, path="/", secure=True)
        self.session.cookies.set(name="signature", value=signature_cookie_value, domain=datasphere_domain,
                                 path="/", secure=True)

        # 2. Request: https://<subdomain>.authentication.eu10.hana.ondemand.com/oauth/authorize
        # mit Weiterleitung an: https://<subdomain>.authentication.eu10.hana.ondemand.com/login
        self.session.headers.update({
            "DNT": "1",
            "Referer": f"{DATASPHERE_URL}/",
            "Accept-Language": "de",
        })
        response = self.session.get(url=oauth_url)

        # SAML Link parsen
        soup = BeautifulSoup(response.text, "html.parser")
        saml_url = f"{AUTH_URL}/" + soup.find("a")["href"]

        # 4. Request: https://<subdomain>.authentication.eu10.hana.ondemand.com/saml/discovery
        # Weiterleitung an: https://<subdomain>.authentication.eu10.hana.ondemand.com/saml/login/alias/<subdomain>.aws-live-eu10
        # erneute Weiterleitung an: https://login.microsoftonline.com/<tenant_id>/saml2 
        self.session.headers.update({
            "Referer": f"{AUTH_URL}/login"
        })
        response = self.session.get(url=saml_url)

        # Prüfen, ob Bestätigung per MFA erforderlich (nicht mehr im Hintergrund angemeldet)
        if not "<title>Working...</title>" in response.text:

            # Kurze Meldung und Zeit zum lesen
            logger.debug("Bestätigung per MFA erforderlich...\n\n")
            sleep(2)

            # Prompt für Username und Password via Rich
            prompt = Prompt()
            console.print("Bitte E-Mail-Adresse zur Anmeldung via Microsoft SSO eingeben.")
            email = prompt.ask("\nE-Mail-Adresse")
            console.print(("\nBitte Passwort des Microsoft Kontos eingeben."))
            console.print("Achtung: Die Eingabe ist maskiert und wird deshalb nicht angezeigt.", style="bold yellow")
            password = prompt.ask("\nPasswort", password=True)
            console.print("\nGeneriere MFA-Code...")

            # Config parsen
            config_data = re.search(r'\$Config=({.*?});', response.text).group(1)
            config_data = json.loads(config_data)
            correlation_id = config_data["correlationId"]

            # 5. Request: https://login.microsoftonline.com/common/GetCredentialType?mkt=de-DE  # TODO: Params parsen?
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
            self.session.headers.update({
                "hpgid": str(config_data["hpgid"]),
                "hpgact": str(config_data["hpgact"]),
                "canary": config_data["apiCanary"],
                "client-request-id": correlation_id,
                "Accept": "application/json",
                "hpgrequestid": config_data["sessionId"],
                "DNT": "1",
                "Origin": "https://login.microsoftonline.com",
                "Referer": response.url
            })
            response = self.session.post(url=url, json=data)
            flow_token = response.json()["FlowToken"]

            # Headers wieder entfernen
            for header in ("hpgid", "hpgact", "canary", "client-request-id", "hpgrequestid"):
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
                "i19": i19
            }
            self.session.headers.update({
                "Origin": "https://login.microsoftonline.com",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng," \
                          "*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
            })
            response = self.session.post(url=login_url, data=data)

            # Neue Config parsen
            config_data = re.search(r'\$Config=({.*?});', response.text).group(1)
            config_data = json.loads(config_data)

            # 7. Request: https://login.microsoftonline.com/common/SAS/BeginAuth
            begin_auth_url = "https://login.microsoftonline.com/common/SAS/BeginAuth"
            data = {
                "AuthMethodId": "PhoneAppNotification",
                "Method": "BeginAuth",
                "ctx": config_data["sCtx"],
                "flowToken": config_data["sFT"]
            }
            self.session.headers.update({
                "hpgid": str(config_data["hpgid"]),
                "hpgact": str(config_data["hpgact"]),
                "canary": config_data["apiCanary"],
                "client-request-id": config_data["correlationId"],
                "Accept": "application/json",
                "hpgrequestid": config_data["sessionId"],
                "Origin": "https://login.microsoftonline.com",
                "Referer": response.url
            })
            response = self.session.post(url=begin_auth_url, json=data)
            auth_data = response.json()

            # Authenticator Code ausgeben
            if str(auth_data["Entropy"]) == 0:
                console.print("Unbekannter Fehler. Bitte erneut starten.", style="bold red")
                sys.exit()
            entropy = auth_data["Entropy"]
            console.print(f"Authenticator Code: {entropy}", style="bold green")

            # 8. Request: https://login.microsoftonline.com/common/SAS/EndAuth
            end_auth_url = "https://login.microsoftonline.com/common/SAS/EndAuth"
            poll_count = 1
            params = {
                "authMethodId": "PhoneAppNotification",
                "pollCount": poll_count
            }
            self.session.headers.update({
                "x-ms-flowToken": auth_data["FlowToken"],
                "x-ms-ctx": auth_data["Ctx"],
                "client-request-id": auth_data["CorrelationId"],
                "x-ms-sessionId": auth_data["SessionId"]
            })

            # 9. Request (und weitere Requests) um Stand der MFA zu prüfen
            console.print("\nWarte auf Bestätigung...")
            last_poll_start_time = None
            last_poll_end_time = None
            flow_token = None
            ctx = None
            while True:
                last_poll_start_time = round(time() * 1000)
                challenge_data = self.session.get(url=end_auth_url, params=params).json()
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
            process_auth_url = "https://login.microsoftonline.com/common/SAS/ProcessAuth"
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
                "i19": i19 + randint(500, 1000)
            }
            for headers in ("hpgid", "hpgact", "canary", "client-request-id", "hpgrequestid", "x-ms-flowToken",
                            "x-ms-ctx", "x-ms-sessionId"):
                self.session.headers.pop(headers)
            self.session.headers.update({
                "Origin": "https://login.microsoftonline.com",
                "Upgrade-Insecure-Requests": "1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng," \
                          "*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            })
            response = self.session.post(url=process_auth_url, data=data)
            self.session.headers.pop("Origin")

        # Werte parsen
        soup = BeautifulSoup(response.text, "html.parser")
        saml_response = soup.find("input", attrs={"name": "SAMLResponse"})["value"]
        relay_state = soup.find("input", attrs={"name": "RelayState"})["value"]

        # 5. / 11. Request: https://<subdomain>.authentication.eu10.hana.ondemand.com/saml/SSO/alias/<subdomain>.aws-live-eu10
        # Weiterleitung an: https://<subdomain>.authentication.eu10.hana.ondemand.com/oauth/authorize
        # Weiterleitung an: https://<subdomain>.eu10.hcs.cloud.sap/sso/login/callback
        data = {
            "SAMLResponse": saml_response,
            "RelayState": relay_state
        }
        self.session.headers.update({
            "Referer": "https://login.microsoftonline.com/"
        })
        response = self.session.post(url=SSO_URL, data=data)

        # Cookies in Datei speichern
        logger.info("Speichere Cookies...")
        with open(COOKIES_FILE, "w") as cookies_file:
            json.dump([{
                "domain": cookie.domain,
                "name": cookie.name,
                "path": cookie.path,
                "secure": cookie.secure,
                "value": cookie.value
            } for cookie in self.session.cookies], cookies_file)

        # Header vorbereiten auf Methodenaufrufe
        for header in ("Connection", "Upgrade-Insecure-Requests", "DNT", "Referer"):
            self.session.headers.pop(header)

    def _start_browser_login(self) -> None:
        """
        Konfiguriert den Selenium Browser und öffnet Edge zur manuellen Anmeldung.
        Lädt Cookies aus dem Browser und schließt ihn dann wieder.
        """

        # TODO als Idee: edge://version/ bzw. chrome://version/ manuell öffnen und Profillink in Settings Datei kopieren
        # dann Profil mit Selenium laden?

        # Event Listener um alle Cookies zu speichern (da get_cookies() sonst nur Cookies von aktueller Seite abruft)
        # TODO: macht momentan keinen Sinn, weil Session nicht persistent ist, nur eine Stunde
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
        options.add_experimental_option('excludeSwitches', ['enable-logging'])

        # TODO: Hier prüfen, ob Cookies noch funktionieren
        # Funktioniert aktuell nur so halb, nochmal mehr testen
        # Geht evtl. mit Playwright auch einfacher
        # Selenium kann nur Cookies von aktueller Website laden
        # bessere Idee: erst alle Cookies aus Datei filtern, jeweilige Domains einmal laden und dann alle Cookies reinladen
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
        #     loaded = wait.until(EC.text_to_be_present_in_element((By.ID, "__title0"), "SAP Datasphere"))

        #     # Aktualisierte Cookies überschreiben, neue Cookies hinzufügen
        #     if loaded:
        #         for cookie in driver.get_cookies():
        #             for num, stored_cookie in enumerate(all_cookies):
        #                 if cookie["name"] == stored_cookie["name"] and cookie["domain"] == stored_cookie["domain"]:
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
            logger.debug("Lade Datasphere Homepage im Browser. Bitte manuell anmelden...")
            ef_driver.get(url=DATASPHERE_URL)
            wait.until(EC.text_to_be_present_in_element((By.ID, "__title0"), "SAP Datasphere"))

            # Cookies speichern
            logger.info("Speichere Cookies...")
            sleep(3)  # für zusätzliche Sicherheit
            listener.all_cookies.extend(ef_driver.get_cookies())  # um Datasphere Cookies hinzuzufügen
            with open(COOKIES_FILE, "w") as cookie_file:
                json.dump(listener.all_cookies, cookie_file, indent=4, ensure_ascii=True)

            # EventFiringWebDriver beenden
            ef_driver.quit()

    # TODO: Implementieren(?)
    # Damit werden alle Cookies gespeichert, auch die von Redirects. 
    # Muss aber 'plawright install' manuell ausführen, um benötigte Browser zu installieren.
    # Nochmal testen, ob es auch ohne funktioniert
    # Oder Pfad bei Kompilierung mit angeben, dann wird installierter Browser mit geshippt
    # TODO: und auch nochmal testen, wirkte so als ob Login doch noch nicht länger als eine Stunde hält
    # ==> müsste eher initialize_requests_session() so anpassen, dass bei Browser Login auch erstmal Cookies geladen werden
    def _start_browser_login_playwright(self) -> None:
        """
        Konfiguriert den Playwright-Browser und öffnet Edge (oder Chromium) zur manuellen Anmeldung.
        Lädt Cookies aus dem Browser (alle Domains) und speichert sie dann.
        """
        from playwright.sync_api import sync_playwright

        # Playwright-Browser starten
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            # Seite öffnen und auf Login warten
            logger.debug("Lade Datasphere Homepage im Browser. Bitte manuell anmelden...")
            page.goto(DATASPHERE_URL)
            page.wait_for_selector('#__title0', timeout=300000)  # 5 Minuten Timeout

            # Kurze Wartezeit für zusätzliche Sicherheit
            sleep(3)

            # Alle Cookies aus allen Domains sammeln
            all_cookies = context.cookies()
            logger.info("Speichere Cookies...")
            with open(COOKIES_FILE, "w", encoding="utf-8") as cookie_file:
                json.dump(all_cookies, cookie_file, indent=4, ensure_ascii=True)

            browser.close()


class RemoteTables(DatasphereAutomation):

    def __init__(self, session: requests.Session = None):

        # DatasphereAutomation initialisieren
        super().__init__(session)

    def _get_all_table_names(self) -> StatisticsDict:
        """
        Gibt alle Tabellennamen als formatiertes Dictionary zurück.

        Returns:
            dict: Dictionary mit Tabellennamen als Schlüssel und einem weiteren Dictionary mit Informationen als Wert.
        """

        # Alle Tabellennamen auslesen
        response = self.session.get(url=f"{DATASPHERE_URL}/dwaas-core/statistics/BWBRIDGESPACE" \
                                        f"/remotetables?includeBusinessNames=true", json={"includeBusinessNames": True})
        all_tables = {
            table["tableName"]: {
                "statisticsSupported": table.get("statisticsSupported", True),
                "statisticsLimitedToRecordCount": table.get("statisticsLimitedToRecordCount", False),
                "statisticsType": table.get("statisticsType"),
                "businessName": table.get("businessName"),
                "statisticsLatestUpdate": table.get("statisticsLatestUpdate")
            }
            for table in response.json()["tables"]
        }

        # Alle Werte bei "statisticsLatestUpdate" in Datetime-Objekt mit korrekter Zeitzone umwandeln
        for table in all_tables.values():
            if table["statisticsLatestUpdate"]:
                converted_dt = datetime.strptime(table["statisticsLatestUpdate"], "%Y-%m-%d %H:%M:%S.%f000000")
                converted_dt = converted_dt.replace(tzinfo=tz.gettz('UTC'))
                converted_dt_with_timezone = converted_dt.astimezone(tz.gettz('Europe/Berlin'))
                table["statisticsLatestUpdate"] = converted_dt_with_timezone

        return all_tables

    def create_statistics(self, type: StatisticsType = "HISTOGRAM") -> None:
        """
        Erstellt Statistiken für alle Tabellen.

        Args:
            type (StatisticsType): Typ der Statistik. Standard ist 'HISTOGRAM'.
        """

        # Alle Tabellennamen lesen
        all_tables = self._get_all_table_names()

        # Über alle Tabellennamen iterieren und Statistik erstellen
        for table in all_tables:

            # Nur Statistiken anlegen bei Tabellen, die sie unterstützen
            if all_tables[table]["statisticsSupported"] and not all_tables[table]["statisticsType"] == type:
                if all_tables[table]["statisticsType"] is None:
                    response = self.session.post(url=f"{DATASPHERE_URL}/dwaas-core/statistics"
                                                     f"/BWBRIDGESPACE/remoteTables/{table}?type={type}",
                                                     json={"type": type})
                elif all_tables[table]["statisticsType"] != type:
                    response = self.session.put(url=f"{DATASPHERE_URL}/dwaas-core/statistics"
                                                    f"/BWBRIDGESPACE/remoteTables/{table}?type={type}",
                                                    json={"type": type})
                    
                # Antwort auswerten
                if response.status_code == 500 and "STATISTICS_ALREADY_EXISTS" in response.text:
                    logger.debug(f"Statistik für Tabelle {table} bereits vorhanden. Wird übersprungen...")
                elif response.status_code == 202:
                    logger.info(f"Statistik für Tabelle {table} erstellt.")
                else:
                    logger.error(f"Fehler beim Erstellen der Statistik für Tabelle {table}. "
                                 f"Status Code: {response.status_code}")
                    logger.debug(f"Response: {response.text}\n")

    def refresh_statistics(self, use_threads: bool = True, thread_count: int = 5) -> None:
        """
        Aktualisiert Statistiken für alle Tabellen in der Datei 'table_names.txt'.
        """

        # Alle Tabellennamen lesen
        all_tables = self._get_all_table_names()

        # Funktion, um Statistiken zu aktualisieren
        # Nur Statistiken anlegen bei Tabellen, die sie unterstützen und eine Statistik haben
        def refresh_statistics_for_table(session: requests.Session, table: str) -> None:
            if all_tables[table]["statisticsSupported"] and all_tables[table]["statisticsType"] is not None:
                response = self.session.post(url=f"{DATASPHERE_URL}/dwaas-core/statistics/" \
                                                 f"BWBRIDGESPACE/remoteTables/{table}/refresh")
                if response.status_code == 202:
                    logger.info(f"Statistik für Tabelle {table} aktualisiert.")
                else:
                    logger.error(f"Fehler beim Aktualisieren der Statistik für {table}. "
                                 f"Status Code: {response.status_code}")
                    logger.debug(f"Response: {response.text}\n")

        # Falls Threads genutzt werden sollen
        if use_threads:
            with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
                for table in all_tables:
                    executor.submit(refresh_statistics_for_table, deepcopy(self.session), table)

        # Falls keine Threads genutzt werden sollen
        else:
            # Über alle Tabellennamen iterieren und Statistik aktualisieren
            for table in all_tables:
                refresh_statistics_for_table(self.session, table)


class Views(DatasphereAutomation):

    def __init__(self, session: requests.Session = None):

        # DatasphereAutomation initialisieren
        super().__init__(session)

    def _get_all_views(self) -> list[ViewDetailsDict]:
        """
        Gibt alle Views als Liste von Dictionaries zurück.

        Returns:
            list[ViewDetailsDict]: Liste von Dictionaries mit View-Namen ("name") und detaillierten Informationen.
        """

        # Headers anpassen
        for header in ("X-Csrf-Token", "X-Requested-With", "Priority"):
            try:
                self.session.headers.pop(header)
            except KeyError:
                pass
        self.session.headers.update({
            "Accept": "application/json",
            "Accept-Language": "de",
            "UI5-Timezone": "Europe/Berlin",
            "UI5-Timepattern": "H%3Amm%3Ass",
            "UI5-Datepattern": "dd.MM.yyyy",
            "Cache-Control": "no-cache"
        })

        # Abfrage vorbereiten
        url = f"{DATASPHERE_URL}/deepsea/repository/search/$all"
        params = {
            "$top": 10000,  # kann nicht weggelassen werden, deswegen zu große Anzahl damit alle Einträge geladen werden
            "$skip": 0,
            "whyfound": "true",
            "$count": "true",
            "valuehierarchy": "folder_id",
            "facets": "all",
            "facetlimit": 5,
            "$apply": 'filter(Search.search(query=\'SCOPE:SEARCH_DESIGN (technical_type_description:EQ(S):"View" AND '\
                      '(technical_type:EQ(S):"DWC_REMOTE_TABLE" OR technical_type:EQ(S):"DWC_LOCAL_TABLE" OR ' \
                      'technical_type:EQ(S):"DWC_VIEW" OR technical_type:EQ(S):"DWC_ERMODEL" OR technical_type:EQ(S):' \
                      '"DWC_DATAFLOW" OR technical_type:EQ(S):"DWC_IDT" OR technical_type:EQ(S):"DWC_BUSINESS_ENTITY"' \
                      ' OR technical_type:EQ(S):"DWC_AUTH_SCENARIO" OR technical_type:EQ(S):"DWC_FACT_MODEL" OR ' \
                      'technical_type:EQ(S):"DWC_CONSUMPTION_MODEL" OR technical_type:EQ(S):"DWC_PERSPECTIVE" OR ' \
                      'kind:EQ(S):"sap.dis.dataflow" OR kind:EQ(S):"sap.dwc.dac" OR kind:EQ(S):"sap.repo.folder" OR ' \
                      'kind:EQ(S):"sap.dwc.analyticModel" OR kind:EQ(S):"sap.dwc.taskChain" OR kind:EQ(S):' \
                      '"sap.dis.replicationflow" OR technical_type:EQ(S):"DWC_TRANSFORMATIONFLOW")) *\'))'
        }

        # Anfrage senden
        logger.debug("Lade alle Views...")
        response = self.session.get(url=url, params=urlencode(params, safe="()*", quote_via=quote))
        all_views: list[ViewDetailsDict] = response.json()["value"]

        # Nicht benötigte Headers für weitere Requests wieder entfernen
        for header in ("Origin", "UI5-Timezone", "UI5-Timepattern", "UI5-Datepattern", "Cache-Control"):
            try:
                self.session.headers.pop(header)
            except KeyError:
                pass

        return all_views

    def get_all_views_where_attribute_contains(self, word: str) -> None:
        """
        Gibt alle Views als CSV-Datei aus, die ein Attribut haben, dass das Suchwort enthält.

        Args:
            word (str): Suchwort (case-insensitive).
        """

        # Alle Views abfragen
        all_views = self._get_all_views()

        # Headers anpassen (Voraussetzung: vorher wird immer get_all_view_names() aufgerufen)
        self.session.headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest"
        })

        # Abfrage vorbereiten
        logger.debug(f"Suche nach Views, die ein Attribut haben, dass den Substring '{word}' enthält...")
        params = {
            "ids": "",
            "details": "id,#repairedCsn,#ownerBusinessName,#creatorBusinessName,#repositoryPackage," \
                       "@EnterpriseSearch.enabled,@remote.source,@DataWarehouse.external.schema," \
                       "#objectPathIdentifier,#repositoryPackage,#repositoryValidationDate,hasPendingError," \
                       "#isI18nEnabled",
            "kinds": "entity,view,sap.dwc.ermodel,sap.dis.dataflow,sap.dwc.taskChain,sap.dwc.analyticModel," \
                     "sap.dwc.dac,sap.repo.folder,sap.dis.replicationflow,sap.dis.transformationflow," \
                     "sap.dwc.perspective,sap.dwc.consumptionModel,sap.dwc.factModel,sap.dwc.businessEntity," \
                     "sap.dwc.authscenario"
        }
        for view in all_views:

            # Parameter anpassen
            params["ids"] = view["id"]

            # Request-ID aktualisieren
            self.session.headers.update({"x-request-id": str(uuid4()).replace("-", ""),})

            # Abfrage senden
            logger.debug(f"Prüfe View {view['name']} in {view['space_name']}...")
            response = self.session.get(url=f"{DATASPHERE_URL}/deepsea/repository"
                                            f"/{view['space_name']}/designObjects", params=params)
            try:
                view_data = response.json()
            except requests.exceptions.JSONDecodeError:
                logger.error(f"Fehler beim Abfragen der View {view['name']} in {view['space_name']}.")
                logger.debug(f"View: {view}\nResponse: {response.text}\n")
                continue

            # Infos in Datei schreiben, falls Attribut mit Suchwort enthalten ist
            for attribute in view_data["results"][0]["#repairedCsn"]["definitions"][view["name"]]["elements"].keys():
                if word.lower() in attribute.lower():
                    logger.info(f"View {view['name']} in {view['space_name']} hat Attribut '{attribute}'.")
                    with open(Datasphere.ALL_FILES["VIEW_ATTRIBUTE"]["absolute_path"], "a", newline="",
                              encoding="utf-8") as file:
                        writer = csv.DictWriter(file, fieldnames=Datasphere.ALL_FILES["VIEW_ATTRIBUTE"]["columns"])
                        values = {
                            "entity": view["name"],
                            "space": view["space_name"],
                            "businessName": view["business_name"],
                            "attribute": attribute
                        }
                        writer.writerow(values)

    def create_view_analytics(self, use_threads: bool = True, thread_count: int = 1) -> None:
        """
        Erstellt View-Analysen für alle Views. Threads können in geringer Anzahl genutzt werden, da es sonst zu
        Ratelimits kommen kann. Fünf Threads sind fehlerfrei durchgelaufen.

        Args:
            use_threads (bool, optional): Wenn True, werden Threads genutzt. Standard ist True.
            thread_count (int, optional): Anzahl an Threads / gleichzeitigen Anfragen. Standard ist 1.
        """

        # Alle Views abfragen
        all_views = self._get_all_views()

        # Headers anpassen (Voraussetzung: vorher wird immer get_all_view_names() aufgerufen)
        self.session.headers.update({
            "x-request-id": str(uuid4()).replace("-", ""),
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest"
        })

        # Tasks starten
        if use_threads:
            lock = threading.Lock()
            with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
                for view in all_views:
                    executor.submit(self._create_view_analytics, view, deepcopy(self.session), lock)
        else:
            for view in all_views:
                self._create_view_analytics(view, self.session)

    def _create_view_analytics(self, view: ViewDetailsDict, session: requests.Session,
                               lock: Optional[threading.Lock] = None, filter_out_own_view: bool = False) -> None:
        """
        Beeinhaltet die Logik zur Erstellung der View-Analysen. Diese Funktion wird über Threads aufgerufen.
        Schreibt alle Views, die in der Analyse mit einem Persistenz-Score von 10 bewertet wurden in eine Datei.

        Args:
            view (ViewDetailsDict): View, für die eine Analyse erstellt wird.
            session (requests.Session): Kopie der Standard-Session (initialiserte Session nach Aufruf 
                                        von get_all_view_names())
            lock (threading.Lock, optional): Threading-Lock für Operationen mit Exportdatei. Standard ist None.
            filter_out_own_view (bool, optional): Wenn True, wird die eigene View aus der Analyse ausgeschlossen.
                                                  Standard ist False.
        """

        # Abfrage vorbereiten
        logger.debug(f"Starte View Analyse für {view['name']} in {view['space_name']}...")
        space_name = view["space_name"]
        view_name = view["name"]
        url = f"{DATASPHERE_URL}/dwaas-core/advisor/{space_name}/execute/{view_name}"
        data = {
            "withMemoryAnalysis": False,
            "maximumMemoryConsumptionInGiB": 1
        }
        response = session.post(url=url, json=data)

        # Auf Fehler prüfen
        if not (response.status_code == 409 and "taskAlreadyRunning" in response.text) \
            and not (response.status_code == 202 and "Running" in response.text):
            logger.error(f"Fehler beim Starten der View Analyse für {view_name} in {space_name}.")
            return
        logger.info(f"View Analyse für {view_name} in {space_name} gestartet.")

        # Request-ID aktualisieren
        session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})

        # Logs der letzten Läufe fetchen
        def fetch_logs() -> list[dict]:
            response = session.get(url=f"{DATASPHERE_URL}/dwaas-core/tf/{space_name}/logs",
                                        params={"objectId": view_name, "getLocks": True})
            return response.json()["logs"]

        # Ergebnisse abwarten
        latest_status = None
        while latest_status != "COMPLETED":
            logs = fetch_logs()
            latest_status = logs[0]["status"]
            if latest_status == "FAILED":
                logger.error(f"Fehler beim Generieren der View Analyse für {view_name} in {space_name}.")
                return 
            # TODO: hier noch aktuelle Laufzeit mit loggen, gibt nur 'startTime': '2025-07-15T07:25:18.803Z' und 'runTime': 239 (in Sekunden)
            logger.debug(f"Warte auf Ergebnisse für {view_name} in {space_name}...")
            sleep(1)

        # Log-ID des letzten Laufs auslesen
        log_id: int = fetch_logs()[0]["logId"]

        # Request-ID aktualisieren
        session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})

        # Ergebnisse auslesen
        response = session.get(url=f"{DATASPHERE_URL}/dwaas-core/advisor/"
                                   f"{space_name}/result/{log_id}")

        # View mit besten Persistenz-Score ermitteln (10 wird nur einmal vergeben)
        # Eigene View rausfiltern, wenn gewünscht, weil sonst kleinere Views immer selber Score 10 erhalten
        entity_stats = response.json()["entityStats"]
        if filter_out_own_view:
            entity_stats = list(filter(lambda entity: entity["entity"] != view_name, entity_stats))
        best_view = list(filter(lambda entity: entity.get("persistencyCandidateScore", 0) == 10, entity_stats))

        # Falls View mit Score 10 gefunden, in Datei schreiben
        if best_view:
            logger.info(f"View {best_view[0]['entity']} in {best_view[0]['space']} hat Persistenz-Score 10.")
            if lock:
                lock.acquire()
            with open(Datasphere.ALL_FILES["VIEW_ANALYSE"]["absolute_path"], "a", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=Datasphere.ALL_FILES["VIEW_ANALYSE"]["columns"])
                writer.writerow({key: best_view[0][key] for key in Datasphere.ALL_FILES["VIEW_ANALYSE"]["columns"]})
            if lock:
                lock.release()
        else:
            logger.debug("Keine View mit Persistenz-Score 10 gefunden.")

    def create_partitioning_for_views(self, partitions: list[str], overwrite_existing_partitions: bool = False) -> None:
        """
        Erstellt Partitionen für alle Views, die in der Datei 'views_to_partition.csv' enthalten sind.
        Benötigt die Task-Datei VIEW_PARTITIONING_FILE_PATH.
        Schreibt Ergebnisse in VIEW_PARTITIONING_RESULT_FILE_PATH.

        Args:
            partitions (list[str]): Liste aller Partitionen, die erstellt werden sollen, in richtiger Reihenfolge.
                                    (Bsp.: ['0000', '2001', '2002', ...]) Letzter Wert ist Obergrenze der letzten 
                                    Partition (Bsp.: FISCYEAR < 2025). Muss deshalb mindestens zwei Werte haben.
            overwrite_existing_partitions (bool, optional): Wenn True, werden bereits existierende Partitionen
                                                            überschrieben. Andernfalls bleiben sie bestehen.
                                                            Standard ist False.
        """

        # Task-Datei lesen
        views_to_partition = []
        with open(Datasphere.ALL_FILES["VIEW_PARTITIONING_CREATE"]["absolute_path"], "r", newline="",
                  encoding="utf-8") as file:
            reader = csv.DictReader(file, fieldnames=Datasphere.ALL_FILES["VIEW_PARTITIONING_CREATE"]["columns"])
            views_to_partition = list(reader)[1:]

        # Headers anpassen (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Origin")
        self.session.headers.pop("Priority")
        self.session.headers.update({"Accept": "*/*"})

        # Alle Views durchlaufen
        for view in views_to_partition:

            # Prüfen, ob Partition bereits existiert
            self.session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
            response = self.session.get(url=f"{DATASPHERE_URL}/dwaas-core/partitioning"
                                            f"/{view['space']}/persistedViews/{view['entity']}")
            partition_exists = len(response.json()["ranges"]) > 0
            format_check = response.json()["partitioningColumns"][view["attribute"]]["type"] == "cds.String"

            # Prüfen, ob Partitionsspalte ein String ist
            if not format_check:
                logger.error(f"Attribut '{view['attribute']}' der View {view['entity']} in {view['space']} ist "
                             f"kein String. Wird übersprungen...")
                with open(Datasphere.ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"]["absolute_path"], "a", newline="",
                          encoding="utf-8") as file:
                    writer = csv.DictWriter(
                        file,
                        fieldnames=Datasphere.ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"]["columns"]
                    )
                    values = {
                        "entity": view["entity"],
                        "space": view["space"],
                        "attribute": view["attribute"],
                        "createdPartition": False
                    }
                    writer.writerow(values)
                continue

            # In Datei vermerken und überspringen, falls Partition bereits existiert und nicht überschrieben werden soll
            if partition_exists and not overwrite_existing_partitions:
                logger.debug(f"{view['entity']} in {view['space']} ist bereits partitioniert. Wird übersprungen...")
                with open(Datasphere.ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"]["absolute_path"], "a", newline="",
                          encoding="utf-8") as file:
                    writer = csv.DictWriter(
                        file,
                        fieldnames=Datasphere.ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"]["columns"]
                    )
                    values = {
                        "entity": view["entity"],
                        "space": view["space"],
                        "attribute": view["attribute"],
                        "createdPartition": True
                    }
                    writer.writerow(values)
                continue

            # Partitionen erstellen
            logger.debug(f"Erstelle Partitionen für {view['entity']} in {view['space']}...")
            self.session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
            url = f"{DATASPHERE_URL}/dwaas-core/partitioning/{view['space']}" \
                  f"/persistedViews/{view['entity']}"
            data = {
                "remoteSourceName": "",
                "objectName": view["entity"],
                "numParallelPartitions": 1,
                "ranges": [{
                    "id": index+1,
                    "low": {
                        "include": True,
                        "value": partitions[index]
                    },
                    "high": {
                        "include": False,
                        "value": partitions[index+1]
                    },
                    "locked": False
                } for index in range(len(partitions)-1)],
                "column": view["attribute"],
                "columnType": "cds.String",
                "runtimeDataCalculation": "designtime",
                "type": "range"
            }
            response = self.session.post(url=url, json=data)

            # In Datei vermerken
            if response.status_code == 201:
                logger.info(f"Partitionen für {view['entity']} in {view['space']} erstellt.")
            else:
                logger.error(f"Fehler beim Erstellen der Partitionen für {view['entity']} in {view['space']}.")
                logger.debug(f"Response: {response.text}\n")
            with open(Datasphere.ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"]["absolute_path"], "a", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=Datasphere.ALL_FILES["VIEW_PARTITIONING_CREATE_RESULT"]["columns"]
                )
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "attribute": view["attribute"],
                    "createdPartition": True if response.status_code == 201 else False
                }
                writer.writerow(values)

    def remove_partitioning_for_views(self) -> None:
        """
        Entfernt Partitionen für alle Views, die in der Datei 'views_to_delete_partition.csv' enthalten sind.
        Benötigt die Task-Datei VIEW_TO_DELETE_PARTITIONING_FILE_PATH.
        Schreibt Ergebnisse in VIEW_TO_DELETE_PARTITIONING_RESULT_FILE_PATH.
        """

        # Task-Datei lesen
        views_to_delete_partition = []
        with open(Datasphere.ALL_FILES["VIEW_PARTITIONING_DELETE"]["absolute_path"], "r", newline="",
                  encoding="utf-8") as file:
            reader = csv.DictReader(file, fieldnames=Datasphere.ALL_FILES["VIEW_PARTITIONING_DELETE"]["columns"])
            views_to_delete_partition = list(reader)[1:]

        # Headers anpassen (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Origin")
        self.session.headers.pop("Priority")
        self.session.headers.update({"Accept": "*/*"})

        # Alle Views durchlaufen
        for view in views_to_delete_partition:

            # Partition entfernen
            logger.debug(f"Entferne Partition für {view['entity']} in {view['space']}...")
            self.session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
            response = self.session.delete(url=f"{DATASPHERE_URL}/dwaas-core/partitioning"
                                               f"/{view['space']}/persistedViews/{view['entity']}")

            # Fehler prüfen
            if response.status_code != 200:
                logger.error(f"Fehler beim Entfernen der Partition für {view['entity']} in {view['space']}.")
                continue

            # In Datei vermerken
            logger.info(f"Partition für {view['entity']} in {view['space']} entfernt.")
            with open(Datasphere.ALL_FILES["VIEW_PARTITIONING_DELETE_RESULT"]["absolute_path"], "a", 
                      newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=Datasphere.ALL_FILES["VIEW_PARTITIONING_DELETE_RESULT"]["columns"]
                )
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "removedPartition": True
                }
                writer.writerow(values)

    def persist_views(self, use_threads: bool = True, thread_count: int = 1, timer: bool = False) -> None:
        """
        Persistiert Views. Threads können in geringer Anzahl genutzt werden, da es sonst zu
        Ratelimits kommen kann. Fünf Threads sind fehlerfrei durchgelaufen.
        Benötigt die Task-Datei VIEW_PERSIST_TASK_FILE_PATH. Schreibt Ergebnisse in VIEW_PERSIST_RESULT_FILE_PATH.

        Args:
            use_threads (bool, optional): Wenn True, werden Threads genutzt. Standard ist True.
            thread_count (int, optional): Anzahl an Threads / gleichzeitigen Anfragen. Standard ist 1.
            timer (bool, optional): Wenn True, wird die Dauer der Persistierung erfasst. Standard ist False.         
        """

        # Task-Datei lesen
        views_to_persist = []
        with open(Datasphere.ALL_FILES["VIEW_PERSIST"]["absolute_path"], "r", newline="") as file:
            reader = csv.DictReader(file, fieldnames=Datasphere.ALL_FILES["VIEW_PERSIST"]["columns"])
            views_to_persist = list(reader)[1:]

        # Ergebnis-Datei mit Werten vorbefüllen
        with open(Datasphere.ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"], "a", newline="",
                  encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=Datasphere.ALL_FILES["VIEW_PERSIST_RESULT"]["columns"])
            for view in views_to_persist:
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "isPersisted": False,
                    "runtime": None
                }
                writer.writerow(values)

        # Headers anpassen (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Priority")
        self.session.headers.pop("Origin")
        self.session.headers.update({
            "Accept": "*/*",
            "x-request-id": str(uuid4()).replace("-", "")
        })

        # Funktion um Result-Datei zu aktualisieren (erst gesamte Datei einlesen, dann neu schreiben,
        # um entsprechende Zeile zu aktualisieren)
        def set_is_persisted_true(view_name: str, view_space: str, lock: Optional[threading.Lock] = None) -> None:
            if lock:
                lock.acquire()
            df = pd.read_csv(Datasphere.ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"])
            df.loc[(df["entity"] == view_name) & (df["space"] == view_space), "isPersisted"] = True
            df.to_csv(Datasphere.ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"], index=False)
            if lock:
                lock.release()

        # Funktion um Result-Datei zu aktualisieren (erst gesamte Datei einlesen, dann neu schreiben,
        # um entsprechende Zeile zu aktualisieren)
        def update_runtime(view_name: str, view_space: str, runtime: int, lock: Optional[threading.Lock] = None) -> None:
            if lock:
                lock.acquire()
            df = pd.read_csv(Datasphere.ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"], dtype={"runtime": "Int64"})
            df.loc[(df["entity"] == view_name) & (df["space"] == view_space), "runtime"] = runtime
            df.to_csv(Datasphere.ALL_FILES["VIEW_PERSIST_RESULT"]["absolute_path"], index=False)
            if lock:
                lock.release()

        # Tasks starten
        if use_threads:
            lock = threading.Lock()
            with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
                future_to_view_mapping = {
                    executor.submit(self._persist_view, deepcopy(self.session), view["entity"], view["space"]): view
                    for view in views_to_persist
                }
                for future in concurrent.futures.as_completed(future_to_view_mapping):
                    view = future_to_view_mapping[future]
                    success, log_details = future.result()
                    runtime = round(log_details.get("runTime", 0)/1000)
                    if success:
                        set_is_persisted_true(view["entity"], view["space"], lock)
                        if timer and runtime > 0:
                            update_runtime(view["entity"], view["space"], runtime, lock)

        else:
            for view in views_to_persist:
                success, log_details = self._persist_view(self.session, view["entity"], view["space"])
                runtime = round(log_details.get("runTime", -1000)/1000)
                if success:
                    set_is_persisted_true(view["entity"], view["space"])
                    if timer and runtime > 0:
                        update_runtime(view["entity"], view["space"], runtime)

    def _persist_view(self, session: requests.Session, view_name: str, view_space: str) -> tuple[bool, dict]:
        """
        Persistiert eine View. Prüft dabei nicht, ob die View bereits persistiert ist.

        Args:
            session (requests.Session): Kopie der Standard-Session (initialiserte Session).
            view_name (str): Name der View.
            view_space (str): Name des View-Spaces.

        Returns:
            tuple[bool, dict]: True, wenn Persistierung erfolgreich war, sonst False. Dict mit Log-Details.
        """

        # Persistenz starten
        logger.debug(f"Starte Persistierung für {view_name} in {view_space}...")
        url = f"{DATASPHERE_URL}/dwaas-core/tf/directexecute"
        data = {
            "applicationId": "VIEWS",
            "spaceId": view_space,
            "objectId": view_name,
            "activity": "PERSIST"
        }
        response = session.post(url=url, json=data)

        # Ergebnis prüfen und taskLogId parsen
        if response.status_code != 202:
            logger.error(f"Fehler beim Starten der Persistierung für {view_name} in {view_space}. Wird übersprungen...")
            return False, {}
        log_id = response.json()["taskLogId"]

        # Funktion zum Abrufen der Log-Details
        def fetch_log_details() -> dict:
            session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
            response = session.get(url=f"{DATASPHERE_URL}/dwaas-core/tf/{view_space}"
                                       f"/extendedlogs/{log_id}")
            return response.json()["logDetails"]

        # Ergebnisse abwarten
        log_details = {}
        while True:
            log_details = fetch_log_details()
            latest_status = log_details["status"]
            if latest_status == "COMPLETED":
                break
            if latest_status == "FAILED" or (latest_status != "COMPLETED" and latest_status != "RUNNING"):
                logger.error(f"Fehler beim Persistieren von {view_name} in {view_space}.")
                return False, log_details

            # Laufzeit in lesbares Format umwandeln und ausgeben
            milliseconds = log_details["runTime"]
            hours, remainder = divmod(milliseconds, 3600000)
            minutes, seconds = divmod(remainder, 60000)
            seconds, milliseconds = divmod(seconds, 1000)
            logger.debug(f"Warte auf Ergebnisse für {view_name} in {view_space}. "
                         f"Aktuelle Laufzeit: {hours:02}:{minutes:02}:{seconds:02}.")
            sleep(1)

        # Result-Datei aktualisieren (erst gesamte Datei einlesen, dann neu schreiben,
        # um entsprechende Zeile zu aktualisieren)
        logger.info(f"Persistierung für {view_name} in {view_space} abgeschlossen.")
        return True, log_details

    def unpersist_views(self, use_threads: bool = True, thread_count: int = 1) -> None:
        """
        Entfernt Persistenzen für Views. Threads können in geringer Anzahl genutzt werden, da es sonst zu
        Ratelimits kommen kann. Fünf Threads sind fehlerfrei durchgelaufen.
        Benötigt die Task-Datei VIEW_UNPERSIST_TASK_FILE_PATH. Schreibt Ergebnisse in VIEW_UNPERSIST_RESULT_FILE_PATH.

        Args:
            use_threads (bool, optional): Wenn True, werden Threads genutzt. Standard ist True.
            thread_count (int, optional): Anzahl an Threads / gleichzeitigen Anfragen. Standard ist 1.
        """

        # Task-Datei lesen
        views_to_unpersist = []
        with open(Datasphere.ALL_FILES["VIEW_UNPERSIST"]["absolute_path"], "r", newline="") as file:
            reader = csv.DictReader(file, fieldnames=Datasphere.ALL_FILES["VIEW_UNPERSIST"]["columns"])
            views_to_unpersist = list(reader)[1:]

        # Ergebnis-Datei mit Werten vorbefüllen
        with open(Datasphere.ALL_FILES["VIEW_UNPERSIST_RESULT"]["absolute_path"], "a", newline="",
                  encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=Datasphere.ALL_FILES["VIEW_UNPERSIST_RESULT"]["columns"])
            for view in views_to_unpersist:
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "isRemoved": False
                }
                writer.writerow(values)

        # Headers anpassen (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Priority")
        self.session.headers.pop("Origin")
        self.session.headers.update({
            "Accept": "*/*",
            "x-request-id": str(uuid4()).replace("-", "")
        })

        # Funktion, um nur entsprechende Zeile in Result-Datei zu aktualisieren
        def set_is_removed_true(view_name: str, view_space: str, lock: Optional[threading.Lock] = None) -> None:
            """Setzt isRemoved in der Result-Datei für die aktuelle View auf True.
            """
            if lock:
                lock.acquire()
            df = pd.read_csv(Datasphere.ALL_FILES["VIEW_UNPERSIST_RESULT"]["absolute_path"])
            df.loc[(df["entity"] == view_name) & (df["space"] == view_space), "isRemoved"] = True
            df.to_csv(Datasphere.ALL_FILES["VIEW_UNPERSIST_RESULT"]["absolute_path"], index=False)
            if lock:
                lock.release()

        # Tasks starten
        if use_threads:
            lock = threading.Lock()
            with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
                future_to_view_mapping = {
                    executor.submit(self._unpersist_view, deepcopy(self.session), view["entity"], view["space"]): view
                    for view in views_to_unpersist
                }
                for future in concurrent.futures.as_completed(future_to_view_mapping):
                    view = future_to_view_mapping[future]
                    success, _ = future.result()
                    if success:
                        set_is_removed_true(view["entity"], view["space"], lock)
        else:
            for view in views_to_unpersist:
                success, _ = self._unpersist_view(self.session, view["entity"], view["space"])
                if success:
                    set_is_removed_true(view["entity"], view["space"])

    def _unpersist_view(self, session: requests.Session, view_name: str, view_space: str) -> tuple[bool, dict]:
        """
        Entfernt die Persistenz für eine View. Prüft vorher, ob View persistiert ist.

        Args:
            session (requests.Session): Kopie der Standard-Session (initialiserte Session).
            view_name (str): Name der View.
            view_space (str): Name des View-Spaces.

        Returns:
            tuple[bool, dict]: True, wenn Entfernung der Persistenz erfolgreich war, sonst False. Dict mit Log-Details.
        """

        # Prüfen, ob View persistiert ist
        url = f"{DATASPHERE_URL}/dwaas-core/monitor/{view_space}/persistedViews/{view_name}"
        response = session.get(url=url)
        if response.status_code != 200 or "dataPersistency" not in response.json().keys():
            logger.error(f"Fehler beim Prüfen, ob View {view_name} in {view_space} persistiert ist. "
                         f"Statuscode: {response.status_code}. Wird übersprungen...")
            return False, {}
        if response.json()["dataPersistency"] != "Persisted":
            logger.debug(f"View {view_name} in {view_space} ist nicht persistiert. Wird übersprungen...")
            return True, {}

        # Persistenz entfernen
        logger.debug(f"Entferne Persistenz für {view_name} in {view_space}...")
        session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
        url = f"{DATASPHERE_URL}/dwaas-core/tf/directexecute"
        data = {
            "applicationId": "VIEWS",
            "spaceId": view_space,
            "objectId": view_name,
            "activity": "REMOVE_PERSISTED_DATA"
        }
        response = session.post(url=url, json=data)

        # Ergebnis prüfen und taskLogId parsen
        if response.status_code != 202:
            logger.error(f"Fehler beim Entfernen der Persistenz für {view_name} in {view_space}. Wird übersprungen...")
            return False, {}
        log_id = response.json()["taskLogId"]

        # Funktion zum Abrufen der Log-Details
        def fetch_log_details() -> list[dict]:
            session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
            response = session.get(url=f"{DATASPHERE_URL}/dwaas-core/tf/{view_space}"
                                            f"/extendedlogs/{log_id}")
            return response.json()["logDetails"]

        # Ergebnisse abwarten
        log_details = {}
        while True:
            log_details = fetch_log_details()
            latest_status = log_details["status"]
            if latest_status == "COMPLETED":
                break
            if latest_status == "FAILED" or (latest_status != "COMPLETED" and latest_status != "RUNNING"):
                logger.error(f"Fehler beim Entfernen der Persistenz für {view_name} in {view_space}.")
                return False, log_details

            # Laufzeit in lesbares Format umwandeln und ausgeben
            milliseconds = log_details["runTime"]
            hours, remainder = divmod(milliseconds, 3600000)
            minutes, seconds = divmod(remainder, 60000)
            seconds, milliseconds = divmod(seconds, 1000)
            logger.debug(f"Warte auf Ergebnisse für {view_name} in {view_space}. "
                         f"Aktuelle Laufzeit: {hours:02}:{minutes:02}:{seconds:02}.")
            sleep(1)

        # Result-Datei aktualisieren
        logger.info(f"Persistenz für {view_name} in {view_space} entfernt.")
        return True, log_details

    def lock_partitions_until_year(self, year: int) -> None:
        """
        Sperrt Partitionen für alle Views, die in der Datei 'views_to_lock_partitions.csv' enthalten sind. Überspringt
        Views, die keine Partitionen haben. Alle Partitionen MÜSSEN ganzzahlige Werte sein!!
        Benötigt die Task-Datei VIEW_PARTITION_LOCK_FILE_PATH.
        Schreibt Ergebnisse in VIEW_PARTITION_LOCK_RESULT_FILE_PATH.

        Args:
            year (int): Jahr, bis zu dem Partitionen gesperrt werden sollen (einschließlich des Jahres selbst). 
        """

        # Task-Datei lesen
        views_to_lock = []
        with open(Datasphere.ALL_FILES["VIEW_PARTITION_LOCK"]["absolute_path"], "r", newline="") as file:
            reader = csv.DictReader(file, fieldnames=Datasphere.ALL_FILES["VIEW_PARTITION_LOCK"]["columns"])
            views_to_lock = list(reader)[1:]

        # Headers anpassen (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Origin")
        self.session.headers.pop("Priority")
        self.session.headers.update({"Accept": "*/*"})

        for view in views_to_lock:

            # Prüfen, ob Partition bereits existiert
            self.session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
            response = self.session.get(url=f"{DATASPHERE_URL}/dwaas-core/partitioning"
                                            f"/{view['space']}/persistedViews/{view['entity']}")
            partition_exists = len(response.json()["ranges"]) > 0

            # Fehler prüfen
            if not partition_exists:
                logger.error(f"View {view['entity']} in {view['space']} hat keine Partitionen. Wird übersprungen...")
                continue

            # Daten der View abrufen
            view_data = response.json()

            # Partitionen sperren
            logger.debug(f"Sperre Partitionen für {view['entity']} in {view['space']} "
                         f"bis einschließlich Jahr {year}...")
            self.session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
            url = f"{DATASPHERE_URL}/dwaas-core/partitioning/{view['space']}" \
                  f"/persistedViews/{view['entity']}"
            data = {
                "remoteSourceName": view_data["remoteSourceName"],
                "objectName": view_data["objectName"],
                "numParallelPartitions": view_data["numParallelPartitions"],
                "ranges": view_data["ranges"],
                "column": view_data["column"],
                "columnType": view_data["columnType"],
                "runtimeDataCalculation": view_data["runtimeDataCalculation"],
                "type": view_data["type"]
            }
            for partition in data["ranges"]:
                if int(partition["low"]["value"]) <= year:
                    partition["locked"] = True
            response = self.session.post(url=url, json=data)

            # In Datei vermerken
            if response.status_code == 201:
                logger.info(f"Partitionen für {view['entity']} in {view['space']} wurden bis einschließlich Jahr "
                            f"{year} gesperrt.")
            else:
                logger.error(f"Fehler beim Sperren der Partitionen für {view['entity']} in {view['space']}.")
                logger.debug(f"Response: {response.text}\n")
            with open(Datasphere.ALL_FILES["VIEW_PARTITION_LOCK_RESULT"]["absolute_path"], "a", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=Datasphere.ALL_FILES["VIEW_PARTITION_LOCK_RESULT"]["columns"])
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "lockedPartitions": True if response.status_code == 201 else False
                }
                writer.writerow(values)

    def unlock_all_partitions(self) -> None:
        """
        Entsperrt alle Partitionen für alle Views, die in der Datei 'views_to_unlock_partitions.csv' enthalten sind.
        Benötigt die Task-Datei VIEW_PARTITION_UNLOCK_FILE_PATH.
        Schreibt Ergebnisse in VIEW_PARTITION_UNLOCK_RESULT_FILE_PATH.
        """

        # Task-Datei lesen
        views_to_unlock = []
        with open(Datasphere.ALL_FILES["VIEW_PARTITION_UNLOCK"]["absolute_path"], "r", newline="") as file:
            reader = csv.DictReader(file, fieldnames=Datasphere.ALL_FILES["VIEW_PARTITION_UNLOCK"]["columns"])
            views_to_unlock = list(reader)[1:]

        # Headers anpassen (Voraussetzung: vorher wurde keine andere Methode aufgerufen)
        self.session.headers.pop("Origin")
        self.session.headers.pop("Priority")
        self.session.headers.update({"Accept": "*/*"})

        for view in views_to_unlock:

            # Prüfen, ob Partition bereits existiert
            self.session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
            response = self.session.get(url=f"{DATASPHERE_URL}/dwaas-core/partitioning"
                                            f"/{view['space']}/persistedViews/{view['entity']}")
            partition_exists = len(response.json()["ranges"]) > 0

            # Fehler prüfen
            if not partition_exists:
                logger.error(f"View {view['entity']} in {view['space']} hat keine Partitionen. Wird übersprungen...")
                continue

            # Daten der View abrufen
            view_data = response.json()

            # Partitionen entsperren
            logger.debug(f"Entsperre alle Partitionen für {view['entity']} in {view['space']}...")
            self.session.headers.update({"x-request-id": str(uuid4()).replace("-", "")})
            url = f"{DATASPHERE_URL}/dwaas-core/partitioning/{view['space']}" \
                  f"/persistedViews/{view['entity']}"
            data = {
                "remoteSourceName": view_data["remoteSourceName"],
                "objectName": view_data["objectName"],
                "numParallelPartitions": view_data["numParallelPartitions"],
                "ranges": view_data["ranges"],
                "column": view_data["column"],
                "columnType": view_data["columnType"],
                "runtimeDataCalculation": view_data["runtimeDataCalculation"],
                "type": view_data["type"]
            }
            for partition in data["ranges"]:
                partition["locked"] = False
            response = self.session.post(url=url, json=data)

            # In Datei vermerken
            if response.status_code == 201:
                logger.info(f"Partitionen für {view['entity']} in {view['space']} wurden entsperrt.")
            else:
                logger.error(f"Fehler beim Entsperren der Partitionen für {view['entity']} in {view['space']}.")
                logger.debug(f"Response: {response.text}\n")
            with open(Datasphere.ALL_FILES["VIEW_PARTITION_UNLOCK_RESULT"]["absolute_path"], "a", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=Datasphere.ALL_FILES["VIEW_PARTITION_UNLOCK_RESULT"]["columns"]
                )
                values = {
                    "entity": view["entity"],
                    "space": view["space"],
                    "unlockedPartitions": True if response.status_code == 201 else False
                }
                writer.writerow(values)


class AnalyticalModels(DatasphereAutomation):

    def __init__(self, session: requests.Session = None):

        # DatasphereAutomation initialisieren
        super().__init__(session)

    def _get_all_analytical_models(self) -> list[AnalyticalModelsDetailsDict]:
        """
        Gibt alle Analytical Models als Liste von Dictionaries zurück.

        Returns:
            list[AnalyticalModelsDetailsDict]: Liste von Dictionaries mit den Analytical Models.
        """

        # Headers anpassen
        self.session.headers.pop("X-Csrf-Token")
        self.session.headers.pop("X-Requested-With")
        self.session.headers.pop("Priority")
        self.session.headers.update({
            "Accept": "application/json",
            "Accept-Language": "de",
            "UI5-Timezone": "Europe/Berlin",
            "UI5-Timepattern": "H%3Amm%3Ass",
            "UI5-Datepattern": "dd.MM.yyyy",
            "Cache-Control": "no-cache"
        })

        # Alle Analytical Models abrufen
        url = f"{DATASPHERE_URL}/deepsea/repository/search/$all"
        params = {
            "$top": 1000,  # kann nicht weggelassen werden, deswegen zu große Anzahl damit alle Einträge geladen werden
            "$skip": 0,
            "whyfound": "true",
            "$count": "true",
            "valuehierarchy": "folder_id",
            "facets": "all",
            "facetlimit": 5,
            "$apply": 'filter(Search.search(query=\'SCOPE:SEARCH_DESIGN (technical_type_description:EQ(S):' \
                      '"Analysemodell" AND (technical_type:EQ(S):"DWC_REMOTE_TABLE" OR technical_type:EQ(S):' \
                      '"DWC_LOCAL_TABLE" OR technical_type:EQ(S):"DWC_VIEW" OR technical_type:EQ(S):"DWC_ERMODEL" ' \
                      'OR technical_type:EQ(S):"DWC_DATAFLOW" OR technical_type:EQ(S):"DWC_IDT" OR technical_type:' \
                      'EQ(S):"DWC_BUSINESS_ENTITY" OR technical_type:EQ(S):"DWC_AUTH_SCENARIO" OR technical_type:' \
                      'EQ(S):"DWC_FACT_MODEL" OR technical_type:EQ(S):"DWC_CONSUMPTION_MODEL" OR technical_type:' \
                      'EQ(S):"DWC_PERSPECTIVE" OR kind:EQ(S):"sap.dis.dataflow" OR kind:EQ(S):"sap.dwc.dac" OR ' \
                      'kind:EQ(S):"sap.repo.folder" OR kind:EQ(S):"sap.dwc.analyticModel" OR kind:EQ(S):' \
                      '"sap.dwc.taskChain" OR kind:EQ(S):"sap.dis.replicationflow" OR technical_type:EQ(S):' \
                      '"DWC_TRANSFORMATIONFLOW")) *\'))'
        }
        response = self.session.get(url=url, params=urlencode(params, safe="()*", quote_via=quote))
        all_analytical_models: list[AnalyticalModelsDetailsDict] = response.json()["value"]

        # Nicht benötigte Headers für weitere Requests wieder entfernen
        self.session.headers.pop("Origin")
        self.session.headers.pop("UI5-Timezone")
        self.session.headers.pop("UI5-Timepattern")
        self.session.headers.pop("UI5-Datepattern")
        self.session.headers.pop("Cache-Control")

        return all_analytical_models

    def _get_all_analytical_models_from_space(self, space_name: str) -> list[AnalyticalModelsDetailsDict]:
        """
        Gibt alle Analytical Models in einem bestimmten Space zurück.

        Args:
            space_name (str): Name des Spaces.

        Returns:
            list[AnalyticalModelsDetailsDict]: Liste von Dictionaries mit den Analytical Models.
        """

        # Alle analytischen Modelle abrufen
        all_analytical_models_in_space = [model for model in self._get_all_analytical_models()
                                          if model["space_name"] == space_name]
        return all_analytical_models_in_space

    def _get_all_views_for_analytical_model(self, analytical_model_id: str) -> dict[str, dict[str, str]]:
        """
        Gibt alle Views zurück, die in einem Analytical Model genutzt werden.

        Args:
            analytical_model_id (str): ID des Analytical Models.

        Returns:
            dict[str, dict[str, str]]: Dictionary mit Analytical Model-ID als Schlüssel und Dictionary als Wert.
                                       Dieses Dictionary hat als Schlüssel die IDs der Views und als Wert den Namen
                                       der Views.
        """

        # Headers updaten (Voraussetzung: vorher wurde get_all_analytical_models() aufgerufen)
        self.session.headers.update({
            "Accept": "*/*",
            "x-request-id": str(uuid4()).replace("-", "")
        })

        # Details abrufen
        url = f"{DATASPHERE_URL}/deepsea/repository/dependencies/"
        params = {
            "ids": analytical_model_id,
            "recursive": True,
            "impact": True,
            "lineage": True,
            "details": "#spaceName,#spaceLabel,qualified_name,@EndUserText.label,@EnterpriseSearch.enabled,owner," \
                       "deployment_date,modification_date,#objectStatus,#businessType,#technicalType," \
                       "@Analytics.provider,#isViewEntity,@DataWarehouse.remote.connection,#isToolingHidden," \
                       "releaseStateValue,releaseDate,deprecationDate,decommissioningDate," \
                       "@ObjectModel.supportedCapabilities,@DataWarehouse.consumption.external,#columnsCount," \
                       "@Analytics.dbViewType,isMissingColumnLineage",
            "dependencyTypes": "csn.query.from,sap.dis.source,sap.dis.targetOf,sap.dis.replicationflow.source," \
                               "sap.dis.replicationflow.targetOf,sap.dwc.transformationflow.source," \
                               "sap.dwc.transformationflow.targetOf,sap.dwc.idtEntity,csn.derivation.lookupEntity," \
                               "csn.valueHelp.entity"
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
        all_ids = {analytical_model_id: {val[0]: val[1] for val in all_ids}}
        return all_ids

    def get_all_views_for_analytical_models(self, skip_duplicates: bool = False) -> None:
        """
        Speichert alle analytischen Modelle mit den dazugehörigen Views in einer Datei ab.
        Die Datei hat folgende Struktur:
        {"ID des Analytischen Modells": {"name": "Name des Analytischen Modells",
                                         "dependencies": {"ID der View": "Name der View", ...}}}
        
        Args:
            skip_duplicates (bool, optional): Wenn True, werden Views herausgefiltert, die schon in anderen
                                              Analytical Models vorkommen. Standard ist False.
                                              Dieses Feature kann z.B. genutzt werden, um Aufgabenketten zu planen.
        """

        # Alle analytischen Modelle abrufen
        logger.debug("Lade alle Analytischen Modelle...")
        all_analytical_models = self._get_all_analytical_models()
        analytical_models_with_views = {}

        # Alle Views abrufen
        views = Views(self.session)
        all_views_list = [(view["id"], view["space_name"]) for view in views._get_all_views()]

        # Über alle Modelle iterieren
        for model in all_analytical_models:
            logger.debug(f"Lade alle Views für das Analytische Modell '{model['name']}' in '{model['space_name']}'...")
            all_views = self._get_all_views_for_analytical_model(model["id"])

            # Views herausfiltern, die schon in anderen Modellen vorkommen, wenn skip_duplicates = True
            if skip_duplicates:
                for view in deepcopy(all_views[model["id"]]):
                    for saved_model in analytical_models_with_views:
                        if view in analytical_models_with_views[saved_model]["dependencies"].keys():
                            all_views[model["id"]].pop(view)
                            break

            # Analytisches Modell abspeichern
            analytical_models_with_views[model["id"]] = {
                "name": model["name"],
                "dependencies": all_views[model["id"]]
            }

            # Spaces der Views herausfiltern
            logger.debug("Update Views mit Spaces...")
            for view_id, view_name in analytical_models_with_views[model["id"]]["dependencies"].items():
                for view in all_views_list:
                    if view_id == view[0]:
                        analytical_models_with_views[model["id"]]["dependencies"][view_id] = (view[1],
                                                                                              view_name)
                        break

        # Ergebnis speichern
        with open(Datasphere.ALL_FILES["ANALYTICAL_MODELS_ALL_VIEWS"]["absolute_path"], "w") as file:
            json.dump(analytical_models_with_views, file, indent=4)
        logger.info(f"Ergebnisse gespeichert in "
                    f"'{Datasphere.ALL_FILES['ANALYTICAL_MODELS_ALL_VIEWS']['absolute_path']}'.")

    def get_all_views_for_analytical_models_in_space(self, space_name: str, skip_duplicates: bool = False) -> None:
        """
        Speichert alle analytischen Modelle eines bestimmten Spaces mit den dazugehörigen Views in einer Datei ab.
        Die Datei hat folgende Struktur:
        {"ID des Analytischen Modells": {"name": "Name des Analytischen Modells",
                                         "dependencies": {"ID der View": ["Space der View", "Name der View"], ...}}}

        Args:
            space_name (str): Name des Spaces.
            skip_duplicates (bool, optional): Wenn True, werden Views herausgefiltert, die schon in anderen
                                              Analytical Models vorkommen. Standard ist False.
                                              Dieses Feature kann z.B. genutzt werden, um Aufgabenketten zu planen.
        """

        # Alle analytischen Modelle abrufen
        logger.debug(f"Lade alle Analytischen Modelle aus dem Space '{space_name}'...")
        all_analytical_models_in_space = self._get_all_analytical_models_from_space(space_name)

        # Alle Views abrufen
        views = Views(self.session)
        all_views_list = [(view["id"], view["space_name"]) for view in views._get_all_views()]

        # Über alle Modelle iterieren
        analytical_models_with_views_in_space = {}
        for model in all_analytical_models_in_space:
            logger.debug(f"Lade alle Views für das Analytische Modell '{model['name']}'...")
            all_views = self._get_all_views_for_analytical_model(model["id"])

            # Views herausfiltern, die schon in anderen Modellen vorkommen, wenn skip_duplicates = True
            if skip_duplicates:
                logger.debug("Filtere bereits gefundene Views heraus...")
                for view in deepcopy(all_views[model["id"]]):
                    for saved_model in analytical_models_with_views_in_space:
                        if view in analytical_models_with_views_in_space[saved_model]["dependencies"].keys():
                            all_views[model["id"]].pop(view)
                            break

            # Analytisches Modell abspeichern
            analytical_models_with_views_in_space[model["id"]] = {
                "name": model["name"],
                "dependencies": all_views[model["id"]]
            }

            # Spaces der Views herausfiltern
            logger.debug("Update Views mit ihren Spaces...")
            for view_id, view_name in analytical_models_with_views_in_space[model["id"]]["dependencies"].items():
                for view in all_views_list:
                    if view_id == view[0]:
                        analytical_models_with_views_in_space[model["id"]]["dependencies"][view_id] = (view[1],
                                                                                                       view_name)
                        break

        # Ergebnis speichern
        file_name = Datasphere.ALL_FILES["ANALYTICAL_MODELS_ALL_VIEWS_IN_SPACE"]["absolute_path"].replace("space",
                                                                                                          space_name)
        with open(file_name, "w") as file:
            json.dump(analytical_models_with_views_in_space, file, indent=4)
        logger.info(f"Ergebnisse gespeichert in '{file_name}'.")

    def check_runtime_for_all_views_of_analytical_models(self, use_threads: bool = True, thread_count: int = 1) -> None:
        """
        Prüft die Laufzeit aller Views für die Analytischen Modelle, die in der Datei
        ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME gespeichert sind. Persistiert dafür die Views und entpersistiert
        anschließend wieder die Views, die nicht bereits persistiert waren.
        Speichert das Ergebnis in der Datei ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME_RESULT.

        Args:
            use_threads (bool, optional): Wenn True, werden die Tasks parallel ausgeführt. Standard ist True.
            thread_count (int, optional): Anzahl der Threads, die parallel ausgeführt werden sollen. Standard ist 1.
        """

        # Task-Datei lesen
        models_to_check = []
        file_name = Datasphere.ALL_FILES["ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME"]["absolute_path"]
        with open(file_name, "r", newline="") as file:
            reader = csv.DictReader(
                file,
                fieldnames=Datasphere.ALL_FILES["ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME"]["columns"]
            )
            models_to_check = list(reader)[1:]

        # Alle Analytischen Modelle abrufen und Mapping von ID erstellen (brauche ich für Methode)
        logger.debug("Lade alle Analytischen Modelle...")
        all_analytical_models = self._get_all_analytical_models()
        models_mapping_id_to_name_and_space = {model["id"]: (model["name"], model["space_name"])
                                               for model in all_analytical_models}

        # Alle Views abrufen
        views = Views(self.session)
        all_views_list = [(view["id"], view["space_name"]) for view in views._get_all_views()]

        # Views für alle Analytischen Modelle abrufen
        analytical_models_with_views = {}
        for model in models_to_check:

            # ID des Analytischen Modells aus dem ID-zu-Namen-und-Space-Mapping filtern
            found = False
            for model_id, (name, space) in models_mapping_id_to_name_and_space.items():
                if model["modelname"] == name and model["space"] == space:
                    found = True

                    # Alle Views für das Analytische Modell abrufen
                    logger.debug(f"Lade alle Views für das Analytische Modell '{model['modelname']}'...")
                    all_views = self._get_all_views_for_analytical_model(model_id)

                    # Views herausfiltern, die schon in anderen Modellen vorkommen, wenn skip_duplicates = True
                    for view in deepcopy(all_views[model_id]):
                        for saved_model in analytical_models_with_views:
                            if view in analytical_models_with_views[saved_model]["dependencies"].keys():
                                all_views[model_id].pop(view)
                                break

                    # Analytisches Modell abspeichern
                    analytical_models_with_views[model_id] = {
                        "name": model["modelname"],
                        "dependencies": all_views[model_id]
                    }

                    # Spaces der Views herausfiltern
                    logger.debug("Update Views mit ihren Spaces...")
                    for view_id, view_name in analytical_models_with_views[model_id]["dependencies"].items():
                        for view in all_views_list:
                            if view_id == view[0]:
                                analytical_models_with_views[model_id]["dependencies"][view_id] = (view[1],
                                                                                                   view_name)
                                break
                    break

            # Prüfen, ob Analytisches Modell gefunden wurde
            if not found:
                logger.error(f"Analytisches Modell '{model['modelname']}' im Space '{model['space']}' "
                             f"wurde nicht gefunden.")

        # Alle Views als Liste filtern
        all_views_to_persist = []
        for model_id, model_data in analytical_models_with_views.items():
            for view_id, (view_space, view_name) in model_data["dependencies"].items():
                all_views_to_persist.append((model_id, view_id, view_space, view_name))

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
                        "removedPersistency": False
                    } for view_id, (view_space, view_name) in model_data["dependencies"].items()
                }
            }

        # Funktion um Runtime zur View hinzuzufügen
        def update_runtime(model_id: str, view_id: str, runtime: int, lock: Optional[threading.Lock] = None) -> None:
            if lock:
                lock.acquire()
            analytical_models_with_views_readable[model_id]["dependencies"][view_id]["runtime"] = runtime
            if lock:
                lock.release()

        # Funktion um Analytische Modelle zu speichern
        file_path_results = Datasphere.ALL_FILES["ANALYTICAL_MODELS_ALL_VIEWS_PERSISTENCE_TIME_RESULT"]["absolute_path"]
        def save_results(lock: Optional[threading.Lock] = None) -> None:
            if lock:
                lock.acquire()
            with open(file_path_results, "w") as file:
                json.dump(analytical_models_with_views_readable, file, indent=4)
            if lock:
                lock.release()

        # Funktion, um Persistenz zu prüfen
        def check_if_persisted(session: requests.Session, view_name: str, view_space: str) -> bool:
            url = f"{DATASPHERE_URL}/dwaas-core/monitor/{view_space}/persistedViews/{view_name}"
            for _ in range(3):
                response = session.get(url=url)
                if response.status_code != 200:
                    sleep(3)
                    continue
                if response.json()["dataPersistency"] == "Persisted":
                    return True
                return False
            
        # Bei allen Views prüfen, ob bereits persistiert
        logger.debug("Prüfe, ob Views bereits persistiert sind...")
        for model_id, model_data in analytical_models_with_views_readable.items():
            for view_data in model_data["dependencies"].values():
                if check_if_persisted(self.session, view_data["name"], view_data["space"]):
                    view_data["alreadyPersisted"] = True
        
        # Erstes Mal Ergebnisse speichern
        logger.debug("Speichere Ergebnisse...")
        save_results()

        # Funktion, für Persistierung und anschließendes Entfernen der Persistenz
        def persist_and_unpersist_view(session: requests.Session, model_id: str, view_id: str, view_space: str,
                                       view_name: str, lock: Optional[threading.Lock] = None) -> None:

            # Persistierung starten
            logger.debug(f"Starte Persistierung von View '{view_name}' in '{view_space}'...")
            persisted, log_details = views._persist_view(session, view_name, view_space)
            runtime = round(log_details.get("runTime", -1000)/1000)

            # Speichern bei erfolgreicher Persistierung
            if persisted:
                logger.info(f"View '{view_name}' in '{view_space}' wurde persistiert.")
                update_runtime(model_id, view_id, runtime if runtime > 0 else None, lock)
                save_results(lock)

                # Persistenz entfernen, wenn nicht vorher persistiert war
                if not analytical_models_with_views_readable[model_id]["dependencies"][view_id]["alreadyPersisted"]:
                    logger.debug(f"Entferne Persistenz von View '{view_name}' in '{view_space}'...")
                    unpersisted, _ = views._unpersist_view(session, view_name, view_space)

                    # Speichern bei erfolgreicher Entpersistierung
                    if unpersisted:
                        logger.info(f"Persistenz von View '{view_name}' in '{view_space}' wurde entfernt.")
                        if lock:
                            lock.acquire()
                        analytical_models_with_views_readable[model_id]["dependencies"][view_id]["removedPersistency"] = True
                        if lock:
                            lock.release()
                        save_results(lock)

                    else:
                        logger.critical(f"Persistenz von View '{view_name}' in '{view_space}' konnte nach "
                                        f"erfolgreicher Persistierung nicht entfernt werden.")
                        logger.critical("Bitte überprüfen.")

                else:
                    logger.debug(f"View '{view_name}' in '{view_space}' war bereits persistiert. "
                                 f"Persistenz wird nicht entfernt.")
                    update_runtime(model_id, view_id, runtime if runtime > 0 else None, lock)
                    save_results(lock)

            else:
                logger.critical(f"View '{view_name}' in '{view_space}' konnte nicht persistiert werden.")
                logger.critical("Bitte überprüfen, ob die View trotzdem persistiert wurde.")

        # Tasks starten und Zeit loggen
        logger.debug("Starte Tasks...")
        if use_threads:
            lock = threading.Lock()
            with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
                for view in all_views_to_persist:
                    executor.submit(persist_and_unpersist_view, deepcopy(self.session), *view, lock)

        else:
            for view in all_views_to_persist:
                persist_and_unpersist_view(self.session, *view)
