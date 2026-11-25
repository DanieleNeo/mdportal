"""Find and launch a local Chromium-family browser without downloading anything."""
import os
import shutil
import sys
from pathlib import Path


def windows_registry_paths(executable):
    """App Paths entries may exist per user or per machine (both registry views)."""
    try:
        import winreg
    except ImportError:
        return
    key = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths' + '\\' + executable
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                    value, _ = winreg.QueryValueEx(handle, None)
                if isinstance(value, str):
                    yield os.path.expandvars(value.strip().strip('"'))
            except OSError:
                continue


def system_candidates(platform=None, environ=None, which=None):
    platform = sys.platform if platform is None else platform
    env = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    if platform == 'win32':
        roots = [env.get(k) for k in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA')]
        # App Paths handles custom installations as well as standard ones.
        for label, exe, rel in (
            ('Google Chrome', 'chrome.exe', 'Google/Chrome/Application/chrome.exe'),
            ('Microsoft Edge', 'msedge.exe', 'Microsoft/Edge/Application/msedge.exe'),
            ('Chromium', 'chromium.exe', 'Chromium/Application/chrome.exe'),
        ):
            for path in windows_registry_paths(exe):
                yield label, Path(path)
            for root in roots:
                if root:
                    yield label, Path(root) / rel
            path = which(exe)
            if path:
                yield label, Path(path)
        return
    if platform == 'darwin':
        for label, name in (('Google Chrome', 'Google Chrome'), ('Microsoft Edge', 'Microsoft Edge'), ('Chromium', 'Chromium')):
            for folder in (Path('/Applications'), Path.home() / 'Applications'):
                yield label, folder / (name + '.app') / 'Contents/MacOS' / name
    for label, names in (
        ('Google Chrome', ('google-chrome', 'google-chrome-stable', 'chrome')),
        ('Microsoft Edge', ('microsoft-edge', 'microsoft-edge-stable', 'msedge')),
        ('Chromium', ('chromium', 'chromium-browser')),
    ):
        for name in names:
            path = which(name)
            if path:
                yield label, Path(path)
    if platform.startswith('linux'):
        for label, path in (('Google Chrome', '/opt/google/chrome/chrome'), ('Microsoft Edge', '/opt/microsoft/msedge/msedge'), ('Chromium', '/snap/bin/chromium')):
            yield label, Path(path)


def launch_browser(chromium, browser_path=None, log=print):
    """Return (browser, info). Explicit paths are authoritative; auto mode retries."""
    if browser_path is not None:
        path = Path(browser_path).expanduser().absolute()
        if not path.is_file():
            raise RuntimeError(f'Browser specificato non trovato: {path}. Correggere --browser-path oppure ometterlo per il rilevamento automatico.')
        candidates = [('Percorso esplicito', path, False)]
    else:
        candidates = [('Chromium Playwright', Path(chromium.executable_path), True)]
        candidates.extend((label, path, False) for label, path in system_candidates())
    failures = []
    seen = set()
    for label, path, managed in candidates:
        if not path.is_file():
            continue
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            continue
        seen.add(key)
        log(f'  [Browser] Tentativo: {label} — {path}', flush=True)
        try:
            # Let Playwright choose its matching headless shell when installed.
            # If only the full Chromium is installed, retry its actual executable.
            if managed:
                try:
                    browser = chromium.launch(headless=True)
                except Exception:
                    browser = chromium.launch(executable_path=str(path), headless=True)
            else:
                browser = chromium.launch(executable_path=str(path), headless=True)
        except Exception as exc:
            reason = next((line.strip() for line in str(exc).splitlines() if line.strip()), type(exc).__name__)
            failures.append(f'{label} ({path}): {reason}')
            log(f'  [Browser] Avvio non riuscito: {reason}', flush=True)
            continue
        info = {'name': label, 'path': str(path), 'version': browser.version, 'selection': 'explicit' if browser_path is not None else 'auto'}
        log(f"  [Browser] Selezionato: {label} {browser.version}", flush=True)
        return browser, info
    details = '\nTentativi falliti:\n- ' + '\n- '.join(failures) if failures else '\nNessun eseguibile compatibile trovato.'
    raise RuntimeError('Impossibile avviare un browser Chromium/Chrome/Edge per il rendering statico.' + details + '\nInstallare Chromium con py -m playwright install chromium, specificare --browser-path PERCORSO oppure usare --render-mode runtime.')
