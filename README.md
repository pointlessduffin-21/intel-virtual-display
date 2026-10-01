# intel-virtual-display

Run a Windows desktop **bigger than your screen**, for example 3840×2160 or even 7680×4320 on a 1920×1080 laptop panel. Windows renders the larger desktop and scales it down to fit the screen. The display keeps receiving its normal native signal, and no driver mod, registry hack or EDID override is involved.

It works like AMD Virtual Super Resolution or NVIDIA DSR, but **Intel graphics has no such feature**, so this tool targets Intel first. It uses a standard Windows mechanism, so it can also work on AMD and NVIDIA machines.

## How it works

Windows can run a display in a *virtual mode*, where the desktop size differs from the signal sent to the screen. Windows' compositor (DWM) draws the full desktop and scales it into a native-resolution image, and the GPU driver only ever sees that native-size image.

Windows normally uses this to show resolutions *lower* than native. This tool requests *higher* ones through the display-configuration API (`SetDisplayConfig` with `SDC_VIRTUAL_MODE_AWARE`). If you ask for a different aspect ratio, the image is letterboxed rather than stretched.

## Quick start

1. Download or clone this repository somewhere permanent, e.g. `C:\Tools\intel-virtual-display`.
2. Open `resolutions\` and double-click a file:

| File | Desktop |
|---|---|
| `2560x1440 (1440p).bat` | 2560×1440 |
| `3200x1800 (QHD+).bat` | 3200×1800 |
| `3840x2160 (4K).bat` | 3840×2160 |
| `5120x2880 (5K).bat` | 5120×2880 |
| `7680x4320 (8K).bat` | 7680×4320 |
| `Native (back to normal).bat` | the display's native resolution |
| `List displays.bat` | shows your displays, GPU vendor and virtual-mode support |

3. A **"Keep changes?"** box appears. Press **Enter** to keep the setting; it's saved and survives a restart. If you do nothing, it reverts after 15 seconds.
4. Go to **Settings → System → Display → Scale** and pick a size that makes text readable at the new resolution.

Settings will show the new resolution as current. These sizes can't be added to the Settings *dropdown*, because that list comes from your GPU driver.

### Apply automatically at sign-in

```bat
startup\install.bat                 :: 3840x2160 on the built-in display
startup\install.bat 2560 1440       :: any size
startup\install.bat 3840 2160 2     :: a specific display (\\.\DISPLAY2)
startup\uninstall.bat
```

This puts a `Virtual Resolution` shortcut in your Startup folder (`shell:startup`). At every sign-in it waits up to 60 s for the display to appear, then applies the resolution silently for that session only. Since the startup run saves nothing, removing the shortcut and restarting fully undoes it. Keep the repository folder where it is after installing.

### Command line

```powershell
.\Set-VirtualResolution.ps1 -List
.\Set-VirtualResolution.ps1 -Width 3840 -Height 2160               # confirm dialog, reverts after 15 s
.\Set-VirtualResolution.ps1 -Width 3840 -Height 2160 -NoPrompt     # apply and save
.\Set-VirtualResolution.ps1 -Width 3840 -Height 2160 -SessionOnly  # apply, don't save
.\Set-VirtualResolution.ps1 -Width 2560 -Height 1440 -Display 2    # auto | internal | primary | 2 | \\.\DISPLAY2
.\Set-VirtualResolution.ps1 -Native
```

The `-Display` setting `auto` (the default) means the built-in panel if there is one, otherwise the primary display. Logs go to `%LOCALAPPDATA%\intel-virtual-display\log.txt`.

## Compatibility

| Setup | Status |
|---|---|
| Intel UHD Graphics (10th gen, Comet Lake), built-in 1920×1080 eDP panel, Windows 11 build 26300 | ✅ **Tested.** 2560×1440, 3200×1800, 3840×2160, 5120×2880 and 7680×4320 all work, panel signal stays 1920×1080. 16:10 desktops are pillarboxed correctly. |
| Same machine, external HP Z24i (1920×1200) over DisplayPort | ❌ Larger-than-native is refused; Windows keeps the desktop at native size. Smaller-than-native works. The tool detects this and leaves the display unchanged. |
| Other Intel iGPUs (6th gen and newer), built-in panels | Expected to work, untested. Check `List displays.bat` → `VirtualModes: yes`. |
| AMD / NVIDIA | Untested. The tool tries virtual modes first, then falls back to asking the driver to scale. If neither works it tells you, and you can use the vendor's own feature: **AMD Virtual Super Resolution** (AMD Software: Adrenalin Edition) or **NVIDIA DSR / DLDSR** (NVIDIA Control Panel / NVIDIA app). These also work on external monitors. |
| Hybrid laptops (Intel/AMD iGPU + NVIDIA dGPU) | The built-in panel is usually driven by the iGPU, which is what matters here. `List displays.bat` shows which GPU drives each display. |
| Windows 10 / 11 | Needs a build with virtual-mode support in the display API (recent Windows 10 and all Windows 11). |

Results from other machines are welcome: open an issue with the `List displays.bat` output and what happened.

## Safety and recovery

- Every change is first applied **for the current session only**. It's saved only when you click Keep, or when you use `-NoPrompt`.
- An independent watchdog process starts *before* the display is touched. If the confirmation never completes (the script crashes, or the dialog isn't visible), the watchdog restores your saved setting after the timeout plus 10 seconds.
- If something looks wrong:
  - double-click `Native (back to normal).bat`;
  - or open Settings → Display and choose the recommended resolution;
  - or, for a session-only change, sign out or restart.
- Only the display you target is changed; other monitors are left alone.

## Limitations

- **Readability:** an 8K desktop on a 14" 1080p panel is downscaled 4×, so raise the Scale setting.
- **Performance:** rendering a larger desktop costs GPU time and memory, which matters most on integrated graphics at 5K and 8K.
- **Sharpness:** scaling quality is whatever Windows' compositor uses. It's good for supersampled text and UI, but it won't add real detail.
- **Monitor changes:** plugging in or removing a monitor makes Windows re-apply its saved layout for that combination of displays. Re-run a batch file and click Keep, or rely on the startup entry at the next sign-in.

## Files

```
Set-VirtualResolution.ps1   main script (list / apply / confirm / watchdog)
Install-Startup.ps1         creates or removes the sign-in shortcut
src/DisplayConfig.cs        Win32 display-configuration (CCD) interop
resolutions/*.bat           one-click resolution switchers
startup/*.bat               install / uninstall the sign-in shortcut
```
