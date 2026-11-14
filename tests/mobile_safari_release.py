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
    driver = subprocess.Popen(["safaridriver", "--port", "4445"])
    failures = []
    try:
        until(lambda: webdriver("GET", "/status") is not None)
        session = webdriver("POST", "/session", {"capabilities": {"alwaysMatch": {
            "browserName": "Safari", "platformName": "iOS",
            "safari:useSimulator": True, "safari:deviceUDID": udid,
        }}})
        base = "/session/" + session["sessionId"]
        def run(method, path, payload=None):
            return webdriver(method, base + path, payload)
        def js(script, *args):
            return run("POST", "/execute/sync", {"script": script, "args": args})
        def goto(url):
            run("POST", "/url", {"url": url})
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
            goto("https://docs.onesa.eu/docs/public/quickstart")
            title = run("GET", "/title")
            return {"passed": "quickstart" in title.lower(), "guide": title}
        item("Anonymous guide deep link and reading", public_guide)

        def public_search():
            goto("https://docs.onesa.eu/")
            # Server-rendered input may be replaced at hydration. Wait for the
            # interactive page, then target the visible search control.
            until(lambda: js("return document.readyState === 'complete'"))
            time.sleep(1)
            box = find('input[aria-label="Search documentation"]')
            run("POST", f"/element/{box}/click", {})
            run("POST", "/actions", {"actions": [{"type": "key", "id": "keyboard", "actions": [
                {"type": kind, "value": char} for char in "quickstart" for kind in ("keyDown", "keyUp")
            ]}]})
            if js("return document.activeElement.value") != "quickstart":
                # iOS WebDriver can leave the OS text-composition field empty.
                # In that case exercise the actual hydrated React input handler
                # with equivalent text and label this separately in the record.
                js("""
                  const input=document.querySelector('input[aria-label="Search documentation"]');
                  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'quickstart');
                  input.dispatchEvent(new InputEvent('input', {bubbles:true,inputType:'insertText',data:'quickstart'}));
                """)
                print("Search input method: Safari DOM input event", flush=True)
            else:
                print("Search input method: WebDriver keystrokes", flush=True)
            time.sleep(2)
            print("Public search state:", js("""
                const region=document.querySelector('[aria-label="Search results"]');
                return {query:document.querySelector('input[aria-label="Search documentation"]').value,
                  status:region?.querySelector('[role=status]')?.innerText,
                  text:region?.innerText.slice(0,70),
                  links:[...(region?.querySelectorAll('a')||[])].slice(0,3).map(a=>a.pathname)};
            """), flush=True)
            selector = '[aria-label="Search results"] a[href*="/docs/public/quickstart"]'
            result = until(lambda: find(selector))
            run("POST", f"/element/{result}/click", {})
            until(lambda: urlsplit(run("GET", "/url")).path.rstrip("/") == "/docs/public/quickstart")
            title = run("GET", "/title")
            return {"passed": "quickstart" in title.lower(), "guide": title}
        item("Anonymous guide search", public_search)

        def public_pdf():
            goto("https://docs.onesa.eu/downloads")
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

        for label, url, condition in [
            ("Company Portal signed-out administration", "https://portal.onesa.eu/admin", "portal"),
            ("Internal documents authentication entry", "https://docs.internal.onesa.eu/", "internal"),
            ("Direct Identity sign-in", "https://auth.onesa.eu/", "identity"),
        ]:
            def observe(url=url, condition=condition):
                goto(url)
                time.sleep(3)
                location = urlsplit(run("GET", "/url"))
                title = run("GET", "/title")
                text = js("return document.body.innerText.toLowerCase().slice(0,5000)")
                has_login = any(word in text for word in ("sign in", "log in", "continue with", "sign-in"))
                passed = has_login and (condition == "portal" or location.hostname == "auth.onesa.eu")
                return {"passed": passed, "host": location.hostname, "path": location.path, "page": title, "sign_in_visible": has_login}
            item(label, observe)
        run("DELETE", "")
    finally:
        driver.terminate()
        driver.wait(timeout=10)
    if failures:
        raise SystemExit("Unverified journeys: " + ", ".join(failures))


if __name__ == "__main__":
    main()
