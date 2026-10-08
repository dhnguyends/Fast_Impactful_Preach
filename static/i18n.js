"use strict";
// English / French interface. The source strings are English: t("…") returns the French version when the
// interface is in French. The static page text is translated once at load (translateDOM), dynamic text goes
// through t(), and messages that come from the server (progress steps, errors) through ts().
// The choice is kept in the "fip_lang" cookie (also read by the server for the public pages).

const LANGS = ["en", "fr"];
const LANG = (() => {
  const m = document.cookie.match(/(?:^|;\s*)fip_lang=(en|fr)/);
  if (m) return m[1];
  return (navigator.language || "").toLowerCase().startsWith("fr") ? "fr" : "en";
})();

function setLang(lang) {
  if (!LANGS.includes(lang) || lang === LANG) return;
  document.cookie = `fip_lang=${lang}; path=/; max-age=31536000; samesite=lax`;
  location.reload();
}

const FR = {
  // ---- top bar ----
  "Upload": "Importer",
  "Clips": "Clips",
  "Projects": "Projets",
  "Choose the social network to publish to": "Choisir le réseau social de publication",
  "Publish to…": "Publier sur…",
  "Channel / account that receives the uploads": "Chaîne / compte qui reçoit les vidéos",
  "Publishing account": "Compte de publication",
  "Claude API key": "Clé API Claude",
  "API key": "Clé API",
  "My projects": "Mes projets",
  "Save this project and start a new video upload": "Enregistrer ce projet et importer une nouvelle vidéo",
  "+ New": "+ Nouveau",
  "Project": " projet",   // inside a flex button: a plain leading space would be dropped
  "Sign out": "Se déconnecter",
  "Interface language": "Langue de l'interface",
  "OK": "OK",
  "Add": "Ajouter",
  "Stop processing": "Arrêter le traitement",

  // ---- home ----
  "Drop a YouTube / video link": "Collez un lien YouTube / vidéo",
  "Continue": "Continuer",
  "Click to browse": "Cliquez pour parcourir",
  "or drag & drop": "ou glissez-déposez",
  "Supported file type: video, audio": "Types acceptés : vidéo, audio",
  "Use a file on this PC": "Utiliser un fichier de ce PC",
  "Clip settings": "Réglages des clips",
  "Number of clips (1–10)": "Nombre de clips (1–10)",
  "Min length (s)": "Durée min. (s)",
  "Max length (s)": "Durée max. (s)",
  "A montage joins 2 to 4 passages of the sermon into one short": "Un montage réunit 2 à 4 passages du sermon dans un short",
  "Montage max length (s)": "Durée max. d'un montage (s)",
  "Language": "Langue",
  "Auto-detect": "Détection auto",
  "Brand name (end card)": "Nom (carton de fin)",
  "Tagline (end card)": "Slogan (carton de fin)",
  "Watermark (optional)": "Filigrane (facultatif)",
  "e.g. Church name": "ex. nom de l'église",
  "Extra guidance for Claude (optional)": "Consignes pour Claude (facultatif)",
  "e.g. focus on the call to repentance": "ex. insister sur l'appel à la repentance",
  "1 clip:": "1 clip :",
  "a video under 3 minutes is already a short — it is used whole. For a longer video:":
    "une vidéo de moins de 3 minutes est déjà un short — elle est utilisée entière. Pour une vidéo plus longue :",
  "Ask me after the video is processed": "Me demander après le traitement de la vidéo",
  "Let Claude find the best moment": "Laisser Claude trouver le meilleur moment",
  "Use this part:": "Utiliser ce passage :",
  "start 12:30": "début 12:30",
  "end 13:45": "fin 13:45",
  "Hook title (top of the short)": "Titre d'accroche (haut du short)",
  "First 15 seconds": "15 premières secondes",
  "Whole short": "Tout le short",
  "Off": "Désactivé",
  "Caption style": "Style des sous-titres",
  "EEIG — sentence case, blue box": "EEIG — casse normale, encadré bleu",
  "Impact — UPPERCASE, bouncy": "Impact — MAJUSCULES, dynamique",
  "Impact highlight": "Mise en valeur (Impact)",
  "Highlight the spoken word": "Surligner le mot prononcé",
  "One keyword per line": "Un mot-clé par ligne",
  "Framing": "Cadrage",
  "Follow the speaker": "Suivre l'orateur",
  "Centre crop": "Recadrage centré",
  "Slides on screen": "Diapositives à l'écran",
  "Show the whole slide": "Montrer toute la diapositive",
  "Crop like the speaker": "Recadrer comme l'orateur",
  "End card logo": "Logo du carton de fin",
  "Shown on the white tile of the end card.": "Affiché sur la tuile blanche du carton de fin.",
  "Change…": "Changer…",
  "Reset": "Réinitialiser",
  "B-roll library": "Bibliothèque de plans de coupe",
  "Your church's own clips, used first when their keywords match.":
    "Les vidéos de votre église, utilisées en priorité quand leurs mots-clés correspondent.",
  "Add clip…": "Ajouter une vidéo…",
  "Double-check start & end of each clip": "Vérifier le début et la fin de chaque clip",
  "Claude may join 2 to 4 passages of the sermon into one short when the idea is developed in several places":
    "Claude peut réunir 2 à 4 passages du sermon dans un short quand l'idée est développée à plusieurs endroits",
  "Montage (several passages per short)": "Montage (plusieurs passages par short)",
  "Emojis": "Emojis",
  "Bouncy punch-in zooms": "Zooms dynamiques",
  "Short cutaway shots over the preacher: the church's library first, then Pixabay":
    "Courts plans de coupe sur le prédicateur : d'abord la bibliothèque de l'église, puis Pixabay",
  "B-roll cutaways": "Plans de coupe (B-roll)",
  "End card": "Carton de fin",
  "auto language": "langue auto",
  "ask for the part": "demander le passage",
  "Claude picks the moment": "Claude choisit le moment",
  "1 clip · {m} · {lang}": "1 clip · {m} · {lang}",
  "{n} clips · {min}–{max}s{montage} · {lang}": "{n} clips · {min}–{max} s{montage} · {lang}",
  " · montage ≤ {max}s": " · montage ≤ {max} s",
  "Number of clips: 1 to 10": "Nombre de clips : de 1 à 10",
  "Enter a valid start and end time (e.g. 12:30 → 13:45)": "Saisissez un début et une fin valides (ex. 12:30 → 13:45)",
  "Project saved — processing continues in the background (My projects)":
    "Projet enregistré — le traitement continue en arrière-plan (Mes projets)",
  "Project saved in My projects": "Projet enregistré dans Mes projets",

  // ---- library / logo ----
  "Otherwise: Pixabay.": "Sinon : Pixabay.",
  "No Pixabay key yet (API key button).": "Pas encore de clé Pixabay (bouton Clé API).",
  "Name": "Nom",
  "Keywords": "Mots-clés",
  "keywords, comma-separated": "mots-clés, séparés par des virgules",
  "Remove from the library": "Retirer de la bibliothèque",
  "Saved": "Enregistré",
  "Remove this clip from the B-roll library?": "Retirer cette vidéo de la bibliothèque de plans de coupe ?",
  "Keywords for “{name}” (comma-separated, French and English), e.g. moisson, blé, harvest, wheat":
    "Mots-clés pour « {name} » (séparés par des virgules, en français et en anglais), ex. moisson, blé, harvest, wheat",
  "Uploading {name}…": "Envoi de {name}…",
  "Upload failed": "Échec de l'envoi",
  "Added to the B-roll library: {name}": "Ajouté à la bibliothèque de plans de coupe : {name}",
  "No logo: the end card shows the church name only.": "Pas de logo : le carton de fin n'affiche que le nom de l'église.",
  "New logo saved ({w}×{h} px). Re-render shorts to apply it.": "Nouveau logo enregistré ({w}×{h} px). Refaites le rendu des shorts pour l'appliquer.",
  "Go back to the default EEIG logo?": "Revenir au logo EEIG par défaut ?",
  "Default logo restored.": "Logo par défaut rétabli.",

  // ---- upload / processing ----
  "Uploading...{pct}%": "Envoi… {pct} %",
  "Upload failed — is the server running?": "Échec de l'envoi — le serveur est-il démarré ?",
  "Cancel": "Annuler",
  "Cancel processing of this video?": "Annuler le traitement de cette vidéo ?",
  "Untitled": "Sans titre",
  "Uploading": "Importation",
  "Please stay on the page while uploading. After that, processing continues on your computer even if you close this tab.":
    "Restez sur la page pendant l'envoi. Ensuite, le traitement continue même si vous fermez cet onglet.",
  "Waiting for your choice": "En attente de votre choix",
  "Something went wrong": "Un problème est survenu",
  "Waiting in queue…": "En file d'attente…",
  "Processing stopped": "Traitement interrompu",
  "Your turn": "À vous",
  "Processing": "Traitement",
  "The sermon is transcribed. Tell us which part to turn into your short.":
    "Le sermon est transcrit. Indiquez la partie à transformer en short.",
  "Fix the problem below, then click Retry — finished steps are kept.":
    "Corrigez le problème ci-dessous, puis cliquez sur Réessayer — les étapes terminées sont conservées.",
  "Claude is watching your sermon. You can close this tab — processing continues on your computer.":
    "Claude regarde votre sermon. Vous pouvez fermer cet onglet — le traitement continue.",
  "Uploading…": "Envoi…",
  "Retry": "Réessayer",
  "Which part should become your short?": "Quelle partie doit devenir votre short ?",
  "This video is": "Cette vidéo dure",
  "long. Let Claude pick the most meaningful moment, or choose it yourself.":
    ". Laissez Claude choisir le moment le plus fort, ou choisissez-le vous-même.",
  "✨ Let Claude find the best moment": "✨ Laisser Claude trouver le meilleur moment",
  "or choose the start and end": "ou choisissez le début et la fin",
  "Start": "Début",
  "End": "Fin",
  "Use the player position": "Utiliser la position du lecteur",
  "⤓ from player": "⤓ depuis le lecteur",
  "Use this part": "Utiliser ce passage",
  "The end must be after the start.": "La fin doit être après le début.",
  "The video ends at {t}.": "La vidéo se termine à {t}.",
  "Clip length: {t}": "Durée du clip : {t}",
  " — long for a short (most are under 90 s)": " — long pour un short (la plupart font moins de 90 s)",
  "Enter a start and end time, e.g. 12:30 and 13:45.": "Saisissez un début et une fin, ex. 12:30 et 13:45.",

  // ---- results ----
  "Shorts": "Shorts",
  "Prepare shorts": "Préparer les shorts",
  "Preparing {done}/{n}…": "Préparation {done}/{n}…",
  "Prepare {n} new short{s} from this sermon? The current shorts ({cur}) will be replaced.":
    "Préparer {n} nouveau{x} short{s} à partir de ce sermon ? Les shorts actuels ({cur}) seront remplacés.",
  "All clips ({n})": "Tous les clips ({n})",
  "Doctrine": "Doctrine",
  "Application": "Application",
  "Repentance": "Repentance",
  "Discipleship": "Discipulat",
  "Gospel": "Évangile",
  "☑ Select All videos to Publish": "☑ Tout sélectionner pour publier",
  "☐ None Video to Publish": "☐ Aucune vidéo à publier",
  "Untick every short": "Décocher tous les shorts",
  "Tick all {n} finished shorts for publication": "Cocher les {n} shorts terminés pour la publication",
  "Rendering…": "Rendu en cours…",
  "Waiting…": "En attente…",
  "On screen:": "À l'écran :",
  "Why this clip?": "Pourquoi ce clip ?",
  "Publish online": "Publier en ligne",
  "Download": "Télécharger",
  "Copy post": "Copier",
  "Adjust": "Ajuster",
  "{n} passages · {d}s": "{n} passages · {d} s",
  "Montage of: {list}": "Montage de : {list}",
  "Montage": "Montage",
  "B-roll: {list}": "Plans de coupe : {list}",
  "Render failed: {err}": "Échec du rendu : {err}",
  "Download this short (MP4)": "Télécharger ce short (MP4)",
  "Rendering failed": "Échec du rendu",
  "Available when the short is ready": "Disponible quand le short est prêt",
  "Post text copied": "Texte copié",
  "Copy:": "Copier :",
  "Select this short for publication": "Sélectionner ce short pour la publication",
  "Online on {net}{where}": "En ligne sur {net}{where}",
  "Sent to {net}{where} (processing)": "Envoyé sur {net}{where} (en traitement)",
  "{net} failed": "Échec sur {net}",
  "Uploading to {net}… {pct}%": "Envoi sur {net}… {pct} %",
  "{net} is processing…": "{net} traite la vidéo…",
  "Queued for {net}…": "En attente pour {net}…",

  // ---- adjust ----
  "Adjust clip": "Ajuster le clip",
  "Pick the first and last sentence. Each clip should open the thought and land the message.":
    "Choisissez la première et la dernière phrase. Chaque clip doit ouvrir l'idée et conclure le message.",
  "This short is a montage: its passages play in this order. Click a passage to edit its first and last sentence; × removes it.":
    "Ce short est un montage : ses passages sont joués dans cet ordre. Cliquez sur un passage pour modifier sa première et sa dernière phrase ; × le retire.",
  "hook": "accroche",
  "Remove this passage": "Retirer ce passage",
  "B-roll": "B-roll",
  "Add a B-roll shot on this sentence": "Ajouter un plan de coupe sur cette phrase",
  "Montage · {n} passages · ": "Montage · {n} passages · ",
  " · passages overlap": " · des passages se chevauchent",
  "Cutaway shots over the preacher. Use \"B-roll\" on a sentence above to add one.":
    "Plans de coupe sur le prédicateur. Utilisez « B-roll » sur une phrase ci-dessus pour en ajouter un.",
  "Add a Pixabay key (API key button) or clips to the B-roll library to use B-roll.":
    "Ajoutez une clé Pixabay (bouton Clé API) ou des vidéos à la bibliothèque pour utiliser les plans de coupe.",
  "Library: {name}": "Bibliothèque : {name}",
  "found at next render": "trouvé au prochain rendu",
  "search (English), e.g. wheat field": "recherche (en anglais), ex. wheat field",
  "Auto": "Auto",
  "Use another search result": "Utiliser un autre résultat de recherche",
  "Other": "Autre",
  "Remove this B-roll": "Retirer ce plan de coupe",
  "No B-roll in this short.": "Aucun plan de coupe dans ce short.",
  "At most 3 B-roll shots per short": "3 plans de coupe au maximum par short",
  "Hook title shown at the top": "Titre d'accroche affiché en haut",
  "(empty = none for this short)": "(vide = aucun pour ce short)",
  "Detect full-screen slides and show them whole; crop on the speaker otherwise":
    "Détecter les diapositives plein écran et les montrer entières ; sinon recadrer sur l'orateur",
  "Crop": "Recadrer",
  "Always crop on the speaker, even on slides": "Toujours recadrer sur l'orateur, même sur les diapositives",
  "Whole frame": "Image entière",
  "Show the whole frame for the entire short (slides, wide shots)":
    "Montrer l'image entière pendant tout le short (diapositives, plans larges)",
  "Re-render": "Refaire le rendu",
  "Re-rendering clip…": "Nouveau rendu du clip…",
  "Please wait until processing finishes.": "Veuillez attendre la fin du traitement.",

  // ---- API key dialog ----
  "Stored only on this computer, in the project's": "Enregistrée uniquement sur cet ordinateur, dans le fichier",
  "file. Get a key at console.anthropic.com.": "du projet. Obtenez une clé sur console.anthropic.com.",
  "Current key": "Clé actuelle",
  "Workspace ID": "ID d'espace de travail",
  "Pixabay key": "Clé Pixabay",
  "none": "aucune",
  "New API key": "Nouvelle clé API",
  "Paste": "Coller",
  "(only if your key is not scoped to a workspace)": "(seulement si votre clé n'est pas liée à un espace de travail)",
  "Pixabay API key": "Clé API Pixabay",
  "(B-roll stock videos — free at pixabay.com/api/docs)": "(vidéos de plans de coupe — gratuite sur pixabay.com/api/docs)",
  "Remove key": "Supprimer la clé",
  "Close": "Fermer",
  "Save": "Enregistrer",
  "Add API key": "Ajouter la clé API",
  "Claude API key {key} · model {model}": "Clé API Claude {key} · modèle {model}",
  "No Claude API key yet — click to add it": "Pas encore de clé API Claude — cliquez pour l'ajouter",
  "Pixabay key saved ({key})": "Clé Pixabay enregistrée ({key})",
  "API key saved ({key})": "Clé API enregistrée ({key})",
  "API key removed": "Clé API supprimée",
  "Clipboard blocked — press Ctrl+V in the field": "Presse-papiers bloqué — appuyez sur Ctrl+V dans le champ",
  "Remove the saved API key and workspace ID from this computer?": "Supprimer la clé API et l'ID d'espace de travail enregistrés ?",
  "Faster for long sermons: the video is read in place, nothing is copied.":
    "Plus rapide pour les longs sermons : la vidéo est lue sur place, rien n'est copié.",

  // ---- publishing ----
  "Hide": "Masquer",
  "Clear": "Effacer",
  "Go Ahead to Publish": "Lancer la publication",
  "Publish to social media": "Publier sur les réseaux sociaux",
  "Choose where your shorts go. You connect once; the app then publishes for you.":
    "Choisissez où vont vos shorts. Vous vous connectez une fois ; l'application publie ensuite pour vous.",
  "Publish": "Publier",
  "Visibility": "Visibilité",
  "Public": "Public",
  "Unlisted (only people with the link)": "Non répertoriée (seulement avec le lien)",
  "Private (only you)": "Privée (vous seul)",
  "channel": "chaîne",
  "Page": "Page",
  "account": "compte",
  "{label} · online": "{label} · en ligne",
  "{label} · not connected": "{label} · non connecté",
  "Connected to {label}{as}": "Connecté à {label}{as}",
  " as {name}": " en tant que {name}",
  "Choose and connect a social network": "Choisir et connecter un réseau social",
  "＋ Add another {kind}…": "＋ Ajouter un(e) autre {kind}…",
  "Uploads go to this {kind}": "Les vidéos sont envoyées sur ce/cette {kind}",
  " (default: {name})": " (par défaut : {name})",
  "Publishing to {net} as {acct}": "Publication sur {net} en tant que {acct}",
  "● Online": "● En ligne",
  "Not connected": "Non connecté",
  "Set up needed": "Configuration requise",
  "Connected to {label}. Uploads go to the selected {kind}{def}:": "Connecté à {label}. Les vidéos vont sur le/la {kind} sélectionné(e){def} :",
  "Remove": "Retirer",
  "＋ Add another {kind}": "＋ Ajouter un(e) autre {kind}",
  "Disconnect all": "Tout déconnecter",
  "Google asks which channel to use each time you add one. If {eeig} is not offered, its owner must add your Google account as a {mgr} of the channel's Brand Account.":
    "Google demande quelle chaîne utiliser à chaque ajout. Si {eeig} n'est pas proposée, son propriétaire doit ajouter votre compte Google comme {mgr} du compte de marque de la chaîne.",
  "Manager": "Gestionnaire",
  "Sign in to {label} to allow this app to publish your shorts.": "Connectez-vous à {label} pour autoriser l'application à publier vos shorts.",
  "Connect {label}": "Connecter {label}",
  "Change app keys ({id})": "Changer les clés de l'application ({id})",
  " (shared by Instagram and Facebook)": " (partagée par Instagram et Facebook)",
  "One-time setup:": "Configuration unique :",
  "{label} only lets registered apps publish. Create your developer app{shared}:":
    "{label} n'autorise que les applications enregistrées à publier. Créez votre application développeur{shared} :",
  "Redirect URI to register:": "URI de redirection à enregistrer :",
  "Copy": "Copier",
  "Client ID (App ID / Client key)": "ID client (App ID / Client key)",
  "Client secret": "Secret client",
  "Save & connect": "Enregistrer et connecter",
  "Redirect URI copied": "URI de redirection copiée",
  "Disconnect every {label} account?": "Déconnecter tous les comptes {label} ?",
  "Remove {name} from FastImpact Preach?": "Retirer {name} de FastImpact Preach ?",
  "Paste both the client ID and the client secret.": "Collez l'ID client et le secret client.",
  "Connected to {label} ✓": "Connecté à {label} ✓",
  "network": "réseau",
  "Choose a social network": "Choisissez un réseau social",
  "Connect {label} first": "Connectez d'abord {label}",
  "{n} short{s} selected": "{n} short{s} sélectionné{s}",
  " for {net}": " pour {net}",
  "Publish {n} short{s} to {net}": "Publier {n} short{s} sur {net}",
  "Publishing {n} short{s} to {net} · {done} of {n} done · {pct}%": "Publication de {n} short{s} sur {net} · {done} sur {n} terminé{s} · {pct} %",
  "{net} is processing the video…": "{net} traite la vidéo…",
  "uploading {pct}%": "envoi {pct} %",
  "Publishing to {net} failed": "Échec de la publication sur {net}",
  "{n} short{s} online on {net}": "{n} short{s} en ligne sur {net}",
  " · {n} still processing on {net}": " · {n} encore en traitement sur {net}",
  "{n} failed": "{n} en échec",
  "{n} kept Private by {net} — see the note on the card. ": "{n} gardé(s) en privé par {net} — voir la note sur la carte. ",
  "Links are on each short's card.": "Les liens sont sur la carte de chaque short.",
  "Publishing to {net} failed.": "Échec de la publication sur {net}.",
  ", {n} still processing": ", {n} encore en traitement",
  ", {n} failed": ", {n} en échec",
  "Uploaded as YouTube Shorts with the title, caption, Bible references and hashtags.":
    "Envoyés comme YouTube Shorts avec le titre, la description, les références bibliques et les hashtags.",
  "Published as Reels with the caption, Bible references and hashtags.":
    "Publiés comme Reels avec la description, les références bibliques et les hashtags.",
  "Published as Reels on your Facebook Page.": "Publiés comme Reels sur votre Page Facebook.",
  "Until TikTok audits your developer app, TikTok only allows private posts.":
    "Tant que TikTok n'a pas audité votre application, seules les publications privées sont permises.",
  " Account: {acct}.": " Compte : {acct}.",
  "Publishing {n} short{s} to {net}… progress is shown below.": "Publication de {n} short{s} sur {net}… la progression s'affiche en bas.",

  // ---- projects ----
  "No projects yet.": "Aucun projet pour l'instant.",
  "Upload a sermon": "Importer un sermon",
  "{n} clips": "{n} clips",
  "Delete project": "Supprimer le projet",
  "Delete": "Supprimer",
  "Delete this project and its clips? This cannot be undone.": "Supprimer ce projet et ses clips ? Cette action est irréversible.",
  "done": "terminé", "error": "erreur", "running": "en cours", "queued": "en attente", "uploading": "envoi",
  "awaiting_input": "à vous", "new": "nouveau", "cancelled": "annulé",

  // ---- login ----
  "FastImpact Preach — Sign in": "FastImpact Preach — Connexion",
  "Sign in": "Connexion",
  "Enter the team password to create sermon shorts.": "Saisissez le mot de passe de l'équipe pour créer des shorts de sermons.",
  "Password": "Mot de passe",
  "Sign-in failed.": "Échec de la connexion.",
  "Please sign in.": "Veuillez vous connecter.",

  // ---- server: progress steps ----
  "Create project": "Création du projet",
  "Process video": "Traitement de la vidéo",
  "Find best parts": "Recherche des meilleurs passages",
  "Edit clips": "Montage des clips",
  "Finalize": "Finalisation",
  "Downloading…": "Téléchargement…",
  "Extracting audio…": "Extraction de l'audio…",
  "Loading speech model…": "Chargement du modèle de transcription…",
  "Claude is analysing your clip…": "Claude analyse votre clip…",
  "Checking that every clip starts and ends cleanly…": "Vérification du début et de la fin de chaque clip…",
  "All clips ready": "Tous les clips sont prêts",
  "Waiting for you: choose the part of the video to turn into a short": "À vous : choisissez la partie de la vidéo à transformer en short",
  "Transcribing (reference Whisper — no live progress)…": "Transcription (Whisper de référence — sans progression)…",
  "Cancelled.": "Annulé.",

  // ---- server: errors ----
  "Too many attempts. Try again in 10 minutes.": "Trop de tentatives. Réessayez dans 10 minutes.",
  "Wrong password.": "Mot de passe incorrect.",
  "Project not found": "Projet introuvable",
  "No logo": "Pas de logo",
  "Please choose an image of at most 5 MB.": "Choisissez une image de 5 Mo au maximum.",
  "This image is too small (at least 64×64 pixels).": "Cette image est trop petite (au moins 64×64 pixels).",
  "This file is not an image (use PNG or JPG).": "Ce fichier n'est pas une image (utilisez PNG ou JPG).",
  "That doesn't look like a Claude API key (it starts with sk-ant-).": "Ce n'est pas une clé API Claude (elle commence par sk-ant-).",
  "Invalid workspace ID.": "ID d'espace de travail invalide.",
  "That doesn't look like a Pixabay API key (digits, a dash, then letters/digits).":
    "Ce n'est pas une clé API Pixabay (des chiffres, un tiret, puis des lettres et chiffres).",
  "B-roll clips must be at most 300 MB.": "Les plans de coupe doivent faire 300 Mo au maximum.",
  "Not found": "Introuvable",
  "Please paste a valid http(s) link.": "Collez un lien http(s) valide.",
  "Reading files from the server is disabled. Upload the video instead.": "La lecture de fichiers du serveur est désactivée. Importez la vidéo.",
  "Unknown project kind": "Type de projet inconnu",
  "The video is not transcribed yet.": "La vidéo n'est pas encore transcrite.",
  "Unknown mode": "Mode inconnu",
  "No transcript yet": "Pas encore de transcription",
  "End must come after start.": "La fin doit venir après le début.",
  "Two passages overlap.": "Deux passages se chevauchent.",
  "Clip not found": "Clip introuvable",
  "Please paste the app's client ID and client secret exactly as shown.": "Collez l'ID client et le secret client exactement comme affichés.",
  "Select at least one finished short.": "Sélectionnez au moins un short terminé.",
  "Source not available": "Source indisponible",
  "B-roll clip not found.": "Plan de coupe introuvable.",
  "This file has no video.": "Ce fichier ne contient pas de vidéo.",
  "This file has no audio track, so there is nothing to transcribe.": "Ce fichier n'a pas de piste audio : il n'y a rien à transcrire.",
  "Could not read the duration of this media file.": "Impossible de lire la durée de ce fichier.",
  "No speech found between these times.": "Aucune parole trouvée entre ces deux temps.",
  "Please enter a valid start and end time (e.g. 12:30 and 13:45).": "Saisissez un début et une fin valides (ex. 12:30 et 13:45).",
  "Claude declined this request.": "Claude a refusé cette demande.",
  "Claude did not return any usable clip.": "Claude n'a renvoyé aucun clip utilisable.",
  "Claude returned no usable answer.": "Claude n'a renvoyé aucune réponse utilisable.",
  "Claude's answer did not contain a list of clips.": "La réponse de Claude ne contenait pas de liste de clips.",
  "Claude's answer was cut off (max_tokens).": "La réponse de Claude a été coupée (max_tokens).",
  "Could not reach the Claude API — check your internet connection.": "Impossible de joindre l'API Claude — vérifiez la connexion Internet.",
  "Your Claude account has no remaining credit. Add credit in the Console, then Retry.":
    "Votre compte Claude n'a plus de crédit. Ajoutez du crédit dans la Console, puis Réessayer.",
  "The Claude API key was rejected (401). Check it in the API key panel (top right).":
    "La clé API Claude a été refusée (401). Vérifiez-la dans le panneau Clé API (en haut à droite).",
  "This API key has no access to the model (403). Check your Console permissions.":
    "Cette clé API n'a pas accès au modèle (403). Vérifiez vos autorisations dans la Console.",
  "Claude rate limit reached (429). Wait a minute, then click Retry.": "Limite de débit Claude atteinte (429). Attendez une minute, puis Réessayer.",
  "Your API key is not scoped to a workspace. Add your Workspace ID in the API key panel (top right), or create a key inside a workspace in the Claude Console.":
    "Votre clé API n'est pas liée à un espace de travail. Ajoutez votre ID d'espace de travail dans le panneau Clé API (en haut à droite), ou créez une clé dans un espace de travail de la Console Claude.",
  "No Claude API key. Add it with the API key button (top right) or set ANTHROPIC_API_KEY in .env.":
    "Pas de clé API Claude. Ajoutez-la avec le bouton Clé API (en haut à droite) ou définissez ANTHROPIC_API_KEY dans .env.",
  "YouTube refused to send this video to the server (\"confirm you're not a bot\": YouTube blocks downloads from data centres). Please upload the video file instead — for your own channel: YouTube Studio → Content → ⋮ next to the video → Download.":
    "YouTube a refusé d'envoyer cette vidéo au serveur (« confirmez que vous n'êtes pas un robot » : YouTube bloque les téléchargements depuis les centres de données). Importez plutôt le fichier vidéo — pour votre propre chaîne : YouTube Studio → Contenu → ⋮ à côté de la vidéo → Télécharger.",
  "Download failed: no file was saved.": "Échec du téléchargement : aucun fichier enregistré.",
  "The rendered video file is missing. Re-render the short first.": "Le fichier vidéo rendu est manquant. Refaites d'abord le rendu du short.",
  "The sign-in link expired or is invalid. Please try connecting again.": "Le lien de connexion a expiré ou n'est pas valide. Reconnectez-vous.",
  "This Google account has no YouTube channel.": "Ce compte Google n'a pas de chaîne YouTube.",
  "This account is not connected.": "Ce compte n'est pas connecté.",
  "YouTube kept it Private (API project not audited yet): make it Public in YouTube Studio.":
    "YouTube l'a gardée en privé (projet API pas encore audité) : rendez-la publique dans YouTube Studio.",
  "Private until TikTok audits your developer app.": "Privée tant que TikTok n'a pas audité votre application.",
  "YouTube's daily upload limit for this channel is reached. Wait about 24 hours, then publish again. To raise the limit, verify the channel by phone at youtube.com/verify.":
    "La limite quotidienne d'envoi de YouTube pour cette chaîne est atteinte. Attendez environ 24 heures, puis publiez à nouveau. Pour relever la limite, vérifiez la chaîne par téléphone sur youtube.com/verify.",
  "The app's YouTube API quota is used up for today (it resets at midnight Pacific time, 09:00 in Geneva). Publish again after that.":
    "Le quota de l'API YouTube de l'application est épuisé pour aujourd'hui (remise à zéro à minuit heure du Pacifique, 9 h à Genève). Publiez à nouveau après.",
  "YouTube is receiving too many requests from the app. Wait a few minutes, then publish again.":
    "YouTube reçoit trop de requêtes de l'application. Attendez quelques minutes, puis publiez à nouveau.",
  "Instagram could not process the video.": "Instagram n'a pas pu traiter la vidéo.",
  "No Facebook Page found for this account (you need admin rights on a Page).": "Aucune Page Facebook pour ce compte (droits d'administrateur requis).",
  "No Instagram Professional account is linked to your Facebook Pages.": "Aucun compte Instagram professionnel n'est lié à vos Pages Facebook.",
};

// Server messages with numbers / names in them.
const FR_RX = [
  [/^Downloading… (\d+)%$/, "Téléchargement… $1 %"],
  [/^Claude is reading the sermon \((\d+) sentences\)…$/, "Claude lit le sermon ($1 phrases)…"],
  [/^Rendering short #(\d+) \((\d+) of (\d+)\)…$/, "Rendu du short n°$1 ($2 sur $3)…"],
  [/^Finding B-roll for short #(\d+)…$/, "Recherche de plans de coupe pour le short n°$1…"],
  [/^Transcribing… (.+) \/ (.+)$/, "Transcription… $1 / $2"],
  [/^(\d+) words · language: (\S+)$/, "$1 mots · langue : $2"],
  [/^1 clip ready$/, "1 clip prêt"], [/^(\d+) clips ready$/, "$1 clips prêts"],
  [/^1 clip proposed$/, "1 clip proposé"], [/^(\d+) clips proposed$/, "$1 clips proposés"],
  [/^(\d+) clip\(s\) failed$/, "$1 clip(s) en échec"],
  [/^Unexpected error: (.*)$/s, "Erreur inattendue : $1"],
  [/^unexpected error \((.*)\)\. See the server log\.$/s, "erreur inattendue ($1). Voir le journal du serveur."],
  [/^Unsupported video type: (.*)$/, "Type de vidéo non pris en charge : $1"],
  [/^Unsupported file type: (.*)$/, "Type de fichier non pris en charge : $1"],
  [/^File not found: (.*)$/, "Fichier introuvable : $1"],
  [/^Download failed: (.*)$/s, "Échec du téléchargement : $1"],
  [/^Could not save the upload: (.*)$/s, "Impossible d'enregistrer l'envoi : $1"],
  [/^Connect (.+) first\.$/, "Connectez d'abord $1."],
  [/^Number of shorts must be between 1 and (\d+)\.$/, "Le nombre de shorts doit être entre 1 et $1."],
  [/^Invalid option: (.*)$/s, "Option invalide : $1"],
  [/^Rendering of (.+) stopped responding and was aborted\.$/, "Le rendu de $1 ne répondait plus et a été interrompu."],
  [/^Claude API error (\d+): (.*)$/s, "Erreur de l'API Claude $1 : $2"],
  [/^The Claude API is temporarily unavailable \((\d+)\)\. Try again shortly\.$/, "L'API Claude est momentanément indisponible ($1). Réessayez bientôt."],
  [/^Not connected to (.+)\.$/, "Non connecté à $1."],
  [/^(\w+) upload failed \((\d+)\): (.*)$/s, "Échec de l'envoi sur $1 ($2) : $3"],
  [/^The (.+) session expired\. Please connect again\.$/, "La session $1 a expiré. Reconnectez-vous."],
  [/^(.+) is not set up yet: add the developer app keys first\.$/, "$1 n'est pas encore configuré : ajoutez d'abord les clés de l'application."],
];

// Interpolate {name} placeholders.
function _fill(s, vars) {
  return vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m)) : s;
}

// Interface string (English source) → current language.
function t(s, vars) {
  return _fill(LANG === "fr" && FR[s] != null ? FR[s] : s, vars);
}

// Message from the server (progress step, error): exact match, "Step: message", or a pattern.
function ts(msg) {
  if (LANG !== "fr" || typeof msg !== "string" || !msg) return msg;
  const key = msg.replace(/\s+/g, " ").trim();
  if (FR[key] != null) return FR[key];
  for (const [rx, rep] of FR_RX) if (rx.test(key)) return key.replace(rx, rep);
  const m = key.match(/^([A-Z][A-Za-z ]{3,30}): (.+)$/s);       // "Find best parts: <error>"
  if (m && FR[m[1]] != null) return `${FR[m[1]]} : ${ts(m[2])}`;
  return msg;
}

// Translate the static page once (text nodes, titles, placeholders, labels — templates included).
function translateDOM(root) {
  if (LANG !== "fr") return;
  const ATTRS = ["title", "placeholder", "aria-label", "alt"];
  const visit = (node) => {
    if (node.nodeType === 3) {
      const key = node.nodeValue.replace(/\s+/g, " ").trim();
      if (key && FR[key] != null) {
        const lead = /^\s/.test(node.nodeValue) ? " " : "", trail = /\s$/.test(node.nodeValue) ? " " : "";
        node.nodeValue = lead + FR[key] + trail;
      }
      return;
    }
    if (node.nodeType !== 1 && node.nodeType !== 11) return;
    if (node.nodeType === 1) {
      if (node.tagName === "SCRIPT" || node.tagName === "STYLE" || node.tagName === "CODE") return;
      for (const a of ATTRS) {
        const v = node.getAttribute(a);
        if (v && FR[v.trim()] != null) node.setAttribute(a, FR[v.trim()]);
      }
      if (node.tagName === "TEMPLATE") visit(node.content);
    }
    for (const c of [...node.childNodes]) visit(c);
  };
  visit(root);
}

// EN | FR switch, placed in the given container.
function mountLangSwitch(container) {
  if (!container) return;
  const box = document.createElement("span");
  box.className = "lang-switch";
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", t("Interface language"));
  box.innerHTML = LANGS.map((l) => `<button type="button" data-lang="${l}" class="${l === LANG ? "on" : ""}"
    aria-pressed="${l === LANG}">${l.toUpperCase()}</button>`).join("");
  box.onclick = (e) => { const b = e.target.closest("[data-lang]"); if (b) setLang(b.dataset.lang); };
  container.prepend(box);
}

document.documentElement.lang = LANG;
translateDOM(document.documentElement);
