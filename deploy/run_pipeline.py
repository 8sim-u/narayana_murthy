"""WAMSS pipeline launcher for agbeast-1.

run-vision.cmd cannot be used to start the pipeline: the WAMSS_API_KEY
value contains < and >, which cmd parses as redirection and aborts with
"The syntax of the command is incorrect". This launcher reads the same
env assignments straight from run-vision.cmd (line based, no cmd
parsing), fixes the working directory to this folder so the relative
yolov8n.pt and data/snapshots paths resolve, and runs
multicamera.py in-process. Works the same when launched from a console
or detached via WMI Win32_Process Create.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VISION_DIR = r"C:\Users\Ayyg\narayana_murthy-design\vision"
SCRIPT = os.path.join(VISION_DIR, "multicamera.py")
CMD_FILE = os.path.join(HERE, "run-vision.cmd")

os.chdir(HERE)

with open(CMD_FILE, "r", encoding="utf-8", errors="replace") as fh:
    for raw in fh:
        line = raw.strip()
        if line.lower().startswith("set ") and "=" in line:
            name, _, value = line[4:].partition("=")
            name = name.strip()
            if name:
                os.environ[name] = value.strip()

# multicamera.py may import sibling modules (identity_cache, face_id,
# room_calib); make sure the vision directory is importable regardless
# of how this launcher itself was started.
if VISION_DIR not in sys.path:
    sys.path.insert(0, VISION_DIR)

print("[launcher] cwd=%s" % os.getcwd())
runpy.run_path(SCRIPT, run_name="__main__")
