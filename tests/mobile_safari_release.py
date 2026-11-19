"""Read-only release checks through Mobile Safari on a hosted iOS simulator."""
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

DRIVER = "http://127.0.0.1:4445"
ELEMENT = "element-6066-11e4-a52e-4f735466cecf"


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def webdriver(method, path, body=None, timeout=45):
    request = urllib.request.Request(
        DRIVER + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response).get("value")
    except urllib.error.HTTPError as error:
        info = json.load(error).get("value", {})
        raise RuntimeError(f"{error.code} {info.get('error', '')}: {str(info.get('message', ''))[:250]}") from None


def until(get_value, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = get_value()
            if value:
                return value
        except (OSError, RuntimeError):
            pass
        time.sleep(1)
    raise RuntimeError("The expected browser state did not appear")


def main():
    devices = json.loads(command("xcrun", "simctl", "list", "-j", "devices", "available"))["devices"]
    candidates = [(runtime, device) for runtime, entries in devices.items() for device in entries
                  if ".iOS-" in runtime and device["name"].startswith("iPhone") and device["isAvailable"]]
    runtime, device = sorted(candidates, key=lambda row: tuple(map(int, row[0].split(".iOS-")[1].split("-"))))[-1]
    udid = device["udid"]
    print("Device:", device["name"], runtime, flush=True)
    if device["state"] != "Booted":
        command("xcrun", "simctl", "boot", udid)
    command("xcrun", "simctl", "bootstatus", udid, "-b")
    command("xcrun", "simctl", "spawn", udid, "defaults", "write", "com.apple.mobilesafari", "AllowRemoteAutomation", "-bool", "YES")
    command("xcrun", "simctl", "spawn", udid, "defaults", "write", "com.apple.webinspectord", "RemoteInspectionEnabled", "-bool", "YES")
    command("sudo", "safaridriver", "--enable")
    command("xcrun", "simctl", "launch", udid, "com.apple.mobilesafari")
    time.sleep(4)
    def launch_driver():
        return subprocess.Popen(["safaridriver", "--port", "4445"])

    driver = launch_driver()
    failures = []
    try:
        # Cold iOS simulators sometimes launch Safari's initial Start Page
        # before their WebDriver HTTP listener becomes responsive. Require the
        # actual WebDriver status, not merely a process or simulator boot.
        def ready():
            if driver.poll() is not None:
                raise SystemExit("SafariDriver exited before its HTTP status was available")
            return webdriver("GET", "/status", timeout=6) is not None

        try:
            until(ready, seconds=70)
        except RuntimeError:
            # No session, navigation, credentials or document action has run.
            # Restart only the unresponsive browser driver and Safari process,
            # never a page interaction or sign-in attempt.
            print("SafariDriver readiness restart before navigation", flush=True)
            driver.terminate()
            driver.wait(timeout=10)
            subprocess.run(["xcrun", "simctl", "terminate", udid, "com.apple.mobilesafari"], check=False)
            command("xcrun", "simctl", "launch", udid, "com.apple.mobilesafari")
            time.sleep(6)
            driver = launch_driver()
            until(ready, seconds=70)
        capabilities = {"capabilities": {"alwaysMatch": {
            "browserName": "Safari", "platformName": "iOS", "pageLoadStrategy": "eager",
            "safari:useSimulator": True, "safari:deviceUDID": udid,
        }}}
        for attempt in range(3):
            try:
                session = webdriver("POST", "/session", capabilities, timeout=90)
                break
            except (RuntimeError, TimeoutError):
                if attempt == 2:
                    raise
                subprocess.run(["xcrun", "simctl", "terminate", udid, "com.apple.mobilesafari"], check=False)
                command("xcrun", "simctl", "launch", udid, "com.apple.mobilesafari")
                time.sleep(4)
        base = "/session/" + session["sessionId"]
        def run(method, path, payload=None):
            return webdriver(method, base + path, payload)
        def js(script, *args):
            return run("POST", "/execute/sync", {"script": script, "args": args})
        def goto(url, redirect_hosts=()):
            target = urlsplit(url)
            # SafariDriver's normal page-load strategy can wait indefinitely for
            # a late resource even after Safari has navigated. Use eager loading,
            # then independently confirm the actual browser URL and DOM state.
            try:
                run("POST", "/url", {"url": url})
            except RuntimeError as error:
                if "timed out" not in str(error).lower():
                    raise

            def reached():
                current = urlsplit(run("GET", "/url"))
                if current.hostname == target.hostname:
                    return current
                if current.hostname in redirect_hosts:
                    return current
                return None

            location = until(reached, seconds=20)
            until(lambda: js("return document.readyState !== 'loading'"), seconds=20)
            return location
        def find(css):
            return run("POST", "/element", {"using": "css selector", "value": css})[ELEMENT]
        def item(label, perform):
            try:
                observation = perform()
                print(json.dumps({"journey": label, **observation}), flush=True)
                if observation.get("passed") is not True:
                    failures.append(label)
            except Exception as error:
                print(json.dumps({"journey": label, "error": str(error)[:400]}), flush=True)
                failures.append(label)

        def public_guide():
            location = goto("https://docs.onesa.eu/docs/public/quickstart")
            title = run("GET", "/title")
            return {"passed": location.hostname == "docs.onesa.eu" and location.path == "/docs/public/quickstart" and "quickstart" in title.lower(), "url": location.geturl(), "guide": title}
        item("Anonymous guide deep link and reading", public_guide)



        def public_pdf():
            location = goto("https://docs.onesa.eu/downloads")
            if location.hostname != "docs.onesa.eu" or location.path.rstrip("/") != "/downloads":
                raise RuntimeError("Public downloads navigation did not reach the downloads page")
            result = run("POST", "/execute/async", {
                "script": """
                const done = arguments[arguments.length - 1];
                (async () => {
                  const link = [...document.querySelectorAll('a[href]')].find(a => decodeURI(a.pathname).endsWith('01 — Onesa — Technical Overview.pdf'));
                  if (!link) throw Error('PDF link absent from downloads');
                  const response = await fetch(link.href);
                  const bytes = await response.arrayBuffer();
                  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2,'0')).join('');
                  done({status: response.status, bytes: bytes.byteLength, digest});
                })().catch(error => done({error: String(error)}));
                """, "args": [],
            })
            manifest = json.loads(Path("public/release.json").read_text())
            expected = manifest["files"]["generated/manuals/01 — Onesa — Technical Overview.pdf"]
            return {**result, "passed": result.get("status") == 200 and result.get("digest") == expected}
        item("Anonymous fetch of the linked public PDF", public_pdf)

        def portal_entry():
            goto("https://portal.onesa.eu/admin")
            def visible_sign_in():
                return js("""return /sign in|log in|continue with|sign-in/i.test(document.body.innerText)""")
            until(visible_sign_in)
            location = urlsplit(run("GET", "/url"))
            return {"passed": location.hostname == "portal.onesa.eu",
                    "host": location.hostname, "path": location.path, "sign_in_visible": True}
        item("Company Portal signed-out administration", portal_entry)

        def internal_entry():
            # A real protected bookmark starts a fresh, browser-bound transaction.
            # The bare issuer hostname redirects to the company homepage.
            goto("https://docs.internal.onesa.eu/", redirect_hosts=("auth.onesa.eu",))
            def provider_location():
                current = urlsplit(run("GET", "/url"))
                if current.scheme == "https" and current.hostname == "auth.onesa.eu":
                    return current
            location = until(provider_location, seconds=30)
            return {"passed": True, "host": location.hostname, "path": location.path}
        item("Internal Documents starts Onesa sign-in", internal_entry)

        def identity_entry():
            def login_form():
                current = urlsplit(run("GET", "/url"))
                if current.scheme != "https" or current.hostname != "auth.onesa.eu":
                    return None
                visible = js("""
                    const form = [...document.forms].find(form => form.getClientRects().length &&
                      [...form.querySelectorAll('input, button')].some(control =>
                        control.getClientRects().length && !control.disabled &&
                        ['email', 'text', 'password', 'submit'].includes(control.type)));
                    return Boolean(form) && /sign in|log in|continue|welcome/i.test(document.body.innerText);
                """)
                return current if visible else None
            location = until(login_form, seconds=30)
            return {"passed": True, "host": location.hostname, "path": location.path,
                    "sign_in_form_visible": True}
        item("Onesa Identity displays sign-in for Internal Documents", identity_entry)

        def search_results():
            home = goto("https://docs.onesa.eu/")
            if home.hostname != "docs.onesa.eu" or home.path not in ("/", "/index.html"):
                return {"passed": False, "reason": "public homepage navigation not confirmed", "url": home.geturl()}
            until(lambda: js("return document.readyState === 'complete'"))
            box = find('input[aria-label="Search documentation"]')
            run("POST", f"/element/{box}/click", {})
            js("""
              const input=document.querySelector('input[aria-label="Search documentation"]');
              Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'quickstart');
              input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:'quickstart'}));
            """)
            selector = '[aria-label="Search results"] a[href*="/docs/public/quickstart"]'
            until(lambda: find(selector))
            target = js("return document.querySelector(arguments[0]).href", selector)
            visible = js("""const e=document.querySelector('[aria-label="Search results"]'); return !!e && e.getClientRects().length > 0""")
            return {
                "passed": visible is True and urlsplit(target).hostname == "docs.onesa.eu"
                    and urlsplit(target).path == "/docs/public/quickstart",
                "result": target,
                "native_keyboard_and_touch": "verified by the separate public-guide-native-touch XCTest job",
            }

        item("Public guide search result discovery", search_results)
        try:
            webdriver("DELETE",base,timeout=3)
        except (RuntimeError,OSError):
            pass
    finally:
        driver.terminate()
        driver.wait(timeout=10)
    if failures:
        raise SystemExit("Unverified journeys: " + ", ".join(failures))


if __name__ == "__main__":
    main()
