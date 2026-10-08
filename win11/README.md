# FastImpact Preach pour Windows 11 / for Windows 11

## Français

**Installer** : lancez `FastImpactPreach-Setup-<version>.exe`. Aucun droit administrateur n'est nécessaire
(installation pour votre compte ; « pour tous les utilisateurs » reste possible). Le composant
« Modèle de transcription » (1,6 Go) est recommandé : sans lui, il est téléchargé au premier usage.

**Utiliser** : ouvrez *FastImpact Preach* depuis le menu Démarrer. L'application s'ouvre dans votre
navigateur (http://localhost:8000) et une petite fenêtre indique qu'elle tourne — gardez-la ouverte,
**Quitter** l'arrête.

- Au premier lancement, cliquez sur **Clé API** (en haut à droite) et collez votre clé Claude
  (console.anthropic.com). Facultatif : clé Pixabay pour les plans de coupe.
- Vos données sont dans `%LOCALAPPDATA%\FastImpactPreach` (bouton **Dossier des données**) :
  projets, clés (`.env`), journaux (`logs\fastimpact.log`). La désinstallation les conserve.
- **Carte NVIDIA** : la transcription utilise la carte graphique si CUDA 12 et cuDNN 9 sont installés,
  sinon le processeur (plus lent, environ 15 min pour 45 min de prédication).
- Publication YouTube depuis ce PC : l'URI de redirection `http://localhost:8000/api/social/youtube/callback`
  doit être enregistrée dans la console Google (voir docs\YouTube_Setup_Guide.docx).

## English

**Install**: run `FastImpactPreach-Setup-<version>.exe`. No administrator rights needed (installs for
your account; "for all users" is also possible). The "speech model" component (1.6 GB) is recommended:
without it, the model downloads on first use.

**Use**: open *FastImpact Preach* from the Start menu. The app opens in your browser
(http://localhost:8000) and a small window shows it is running — keep it open; **Quit** stops it.

- On first use, click **API key** (top right) and paste your Claude key (console.anthropic.com).
  Optional: a Pixabay key for B-roll.
- Your data lives in `%LOCALAPPDATA%\FastImpactPreach` (**Data folder** button): projects, keys
  (`.env`), logs (`logs\fastimpact.log`). Uninstalling keeps it.
- **NVIDIA GPU**: transcription uses the GPU when CUDA 12 and cuDNN 9 are installed, otherwise the CPU
  (slower, about 15 min for a 45-min sermon).
- Publishing to YouTube from this PC: the redirect URI `http://localhost:8000/api/social/youtube/callback`
  must be registered in the Google console (see docs\YouTube_Setup_Guide.docx).

## Building / Construire

```powershell
.\win11\build.ps1            # tests, program folder, speech model, installer
.\win11\build.ps1 -NoModel   # installer without the speech model
```

Requires the project's `.venv` (Python 3.11), FFmpeg on PATH and Inno Setup 6.
Output: `win11\build\dist\FastImpactPreach\` (portable folder) and `win11\installer\` (setup).
