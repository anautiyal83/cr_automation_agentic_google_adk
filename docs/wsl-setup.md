# WSL setup (Windows hosts)

On Windows, step 9 (QA-Execution) runs the cli-automation JAR's `execute` inside WSL, so the workflow's
`node: local` steps run under Linux bash, as on the production automation server. Windows `bash -c` drops double
quotes and rewrites `/word` to `C:/word` (spec 013 P-6, PF-D5). MockWire keeps running on Windows; WSL reaches it
on `localhost` through mirrored networking.

Linux hosts need none of this: leave `execution.wsl_distro` empty.

## 1. Install WSL with Ubuntu 24.04

PowerShell **as Administrator**:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart Windows when asked. Open **Ubuntu 24.04** from the Start menu once and create the Linux user.

Check (PowerShell):

```powershell
wsl -l -v          # Ubuntu-24.04 … VERSION 2
```

## 2. Mirrored networking

Create `%UserProfile%\.wslconfig` (e.g. `C:\Users\<you>\.wslconfig`). The file name has **no** `.txt` extension.
Notepad adds one unless "Save as type" is set to *All files*.

```ini
[wsl2]
networkingMode=mirrored
```

Apply it:

```powershell
wsl --shutdown
```

## 3. Packages inside Ubuntu

```bash
sudo apt update
sudo apt install -y openjdk-8-jre-headless python3 python3-pandas python3-openpyxl
```

| Package | Why |
|---------|-----|
| `openjdk-8-jre-headless` | The cli-automation JAR evaluates `when` / `expr` with the Nashorn JavaScript engine, which Java 15+ no longer ships |
| `python3` | The workflow's helper scripts (`python3 <script>.py …`) |
| `python3-pandas`, `python3-openpyxl` | Helper scripts that read the CIQ / delta Excel files (OB-49) |

The pipeline calls plain `java`. If another Java is installed too, make Java 8 the default:

```bash
sudo update-alternatives --config java     # pick …java-8-openjdk…
```

## 4. Pipeline configuration

`config/pipeline_config.yaml`:

```yaml
execution:
  wsl_distro: "Ubuntu-24.04"     # the distro name from `wsl -l -v`; empty = run on Windows
```

Restart the server (`.\start.ps1`) after a change.

## 5. Verify

PowerShell:

```powershell
wsl -d Ubuntu-24.04 -- java -version                                   # openjdk version "1.8.0_…"
wsl -d Ubuntu-24.04 -- python3 -c "import pandas, openpyxl; print('ok')"
wsl -d Ubuntu-24.04 -- ls /mnt/d/my_workspace/cr_automation_agentic_google_adk/lib   # project visible
```

With a run's simulator up (step 8), WSL reaches MockWire on the Windows port:

```powershell
wsl -d Ubuntu-24.04 -- bash -c "timeout 3 bash -c '</dev/tcp/127.0.0.1/22200' && echo reachable"
```

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Step 9: `wsl.exe` not found / distro not found | WSL not installed, or `wsl_distro` differs from `wsl -l -v` |
| Step 9 cannot connect to the simulator | `.wslconfig` missing, saved as `.wslconfig.txt`, or `wsl --shutdown` not run |
| JAR fails on `when` / `expr` (no JavaScript engine) | A Java newer than 8 is the default: install Java 8, `update-alternatives --config java` |
| `ModuleNotFoundError: No module named 'pandas'` in a local step | `sudo apt install -y python3-pandas python3-openpyxl` |
| `java --version`: "Unrecognized option" | Java 8 only knows `java -version` (one dash), not an error |
