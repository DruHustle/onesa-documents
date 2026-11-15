"""Read-only release checks through Mobile Safari on a hosted iOS simulator."""
import json
import subprocess
import concurrent.futures
import tempfile
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
    driver = subprocess.Popen(["safaridriver", "--port", "4445"])
    failures = []
    try:
        until(lambda: webdriver("GET", "/status") is not None)
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
                if current.hostname == target.hostname and current.path == target.path:
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

        for label, url, condition in [
            ("Company Portal signed-out administration", "https://portal.onesa.eu/admin", "portal"),
            ("Internal documents authentication entry", "https://docs.internal.onesa.eu/", "internal"),
            ("Direct Identity sign-in", "https://auth.onesa.eu/", "identity"),
        ]:
            def observe(url=url, condition=condition):
                location = goto(url, redirect_hosts=("auth.onesa.eu",) if condition == "internal" else ())
                time.sleep(3)
                location = urlsplit(run("GET", "/url"))
                title = run("GET", "/title")
                text = js("return document.body.innerText.toLowerCase().slice(0,5000)")
                has_login = any(word in text for word in ("sign in", "log in", "continue with", "sign-in"))
                passed = has_login and (condition == "portal" or location.hostname == "auth.onesa.eu")
                return {"passed": passed, "host": location.hostname, "path": location.path, "page": title, "sign_in_visible": has_login}
            item(label, observe)
        def result_touch():
            home = goto("https://docs.onesa.eu/")
            if home.hostname != "docs.onesa.eu" or home.path not in ("/", "/index.html"):
                return {"passed": False, "reason": "public homepage navigation not confirmed", "url": home.geturl()}
            until(lambda: js("return document.readyState === 'complete'"))
            time.sleep(1)
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
            point = js("""
              const link=document.querySelector(arguments[0]), title=link.querySelector("strong")||link;
              title.scrollIntoView({block:"nearest",inline:"nearest"});
              const b=title.getBoundingClientRect(), x=Math.round(b.x+b.width/2), y=Math.round(b.y+b.height/2);
              return {x,y,hit:link.contains(document.elementFromPoint(x,y))};
            """, selector)
            if not point["hit"]:
                return {"passed":False,"reason":"first result title occluded","result":target}
            # Read the OS-rendered screen after touch independently of SafariDriver.
            # A timed-out WebDriver command must not prevent the observation or
            # contaminate the earlier independent URL, PDF and Portal journeys.
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                swift = folder / "screen.swift"
                swift.write_text("""
import Vision
import Foundation
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
try VNImageRequestHandler(url: URL(fileURLWithPath: CommandLine.arguments[1])).perform([request])
print((request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: " | "))
""")
                reader = str(folder / "read-screen")
                command("xcrun","swiftc",str(swift),"-o",reader)
                def read_screen():
                    path = str(folder / "display.png")
                    command("xcrun","simctl","io",udid,"screenshot",path)
                    return command(reader,path).lower()
                before = read_screen()
                print("Before touch, Apple Vision from the simulator display:", before[:680], flush=True)
                guide_markers = ("docker-only application startup", "docker-only", "git clone", "cd onesa-ai-platform", "all commands below run", "reproducible review", "compose v2", "python3 scripts/bootstrap")
                if any(marker in before for marker in guide_markers):
                    return {"passed": False, "reason": "homepage precondition still shows guide content", "result": target}
                payload = {"actions":[{"type":"pointer","id":"finger","parameters":{"pointerType":"touch"},"actions":[
                    {"type":"pointerMove","duration":0,"origin":"viewport","x":point["x"],"y":point["y"]},
                    {"type":"pointerDown","button":0},{"type":"pause","duration":100},{"type":"pointerUp","button":0}]}]}
                with concurrent.futures.ThreadPoolExecutor(1) as pool:
                    touch = pool.submit(webdriver, "POST", base+"/actions", payload, 9)
                    after = ""
                    for delay in (2,3,4):
                        time.sleep(delay)
                        after = read_screen()
                        print("After touch, Apple Vision from the simulator display:", after[:840], flush=True)
                        # The linked guide's numbered section is absent from
                        # the closed and open search overlays on the homepage.
                        try:
                            after_url = urlsplit(run("GET", "/url"))
                        except (OSError, RuntimeError):
                            after_url = None
                        changed_to_guide = after_url is not None and after_url.hostname == "docs.onesa.eu" and after_url.path == "/docs/public/quickstart"
                        unique_guide_content = any(marker in after and marker not in before for marker in guide_markers)
                        if "developer quickstart" in after and (changed_to_guide or unique_guide_content):
                            return {"passed":True,"activation":"native Safari touch; independent display OCR","url":after_url.geturl() if after_url else None,"result":target}
                    try:
                        touch.result()
                    except (OSError, RuntimeError) as error:
                        print("SafariDriver touch transport:",str(error)[:160],flush=True)
                    return {"passed":False,"result":target,"reason":"guide page not confirmed from simulator display"}

        item("Anonymous guide search and native result touch", result_touch)
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
