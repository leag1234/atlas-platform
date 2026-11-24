"""What the picture turn says, in the five interface languages.

The tables are written in the polite form; a person who writes in the
familiar form reads every text in that form (packages.addressing, as on the
other answer paths since PR 645): the picture path kept the polite form with
a person writing in the familiar one. FAMILIAR holds
the polite phrases of these tables that the shared table does not.
"""

from __future__ import annotations

import re

from packages.addressing import addressed

LANGUAGES = ("en", "fr", "de", "es", "it")

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "intro": "Here are {n} proposals; the first, framed, is the one I suggest.",
        "single": "Here is the picture.",
        "partial_one": "Only one of the four drafts could be finished.",
        "partial": "Only {n} of the four drafts could be finished.",
        "hint": (
            'Click "Picture 2" (for example) to choose it and see it full size, '
            'or answer "the second". Without a choice, the first one is used for '
            "any change."
        ),
        "links": "Choose:",
        "label": "Picture {n}",
        "sheet": "Proposals 1 to {n}",
        "photo": (
            "These are illustrations drawn by code, not photographs: no realistic "
            "photo is made here."
        ),
        "chosen": "Picture {n} is chosen; changes will apply to it.",
        "missing": "The last proposals have {count} pictures: there is no picture {n}.",
        "missing_shown": "This conversation shows {count} pictures: there is no picture {n}.",
        "cpu_no_edit_server": "The image server in use cannot edit an existing picture: it can only draw a new one.",
        "cpu_no_edit_busy": "The image server is too busy right now to edit the picture within the time of this answer.",
        "cpu_no_edit_failed": "The image server failed to edit the picture this time.",
        "cpu_no_edit_original": "The original of this picture is no longer stored, so it cannot be edited.",
        "cpu_no_edit_offer": 'So nothing was redrawn: version {version} stays as it is. The closest option is to draw it again from its description with this change and the same starting point: the composition stays close, but other details may change. Answer "yes" to do that; an edit of the picture itself can be tried again later.',
        "cpu_redrawn": "This picture was not edited in place: as proposed, it was drawn again from its description with the change, so other details may differ from the previous version.",
        "expired": "These proposals are no longer stored (30-day retention).",
        "reading": "Reading the request…",
        "drawing": "Drawing four proposals…",
        "drawing_done": "Drawing four proposals… {done} of 4 finished",
        "finishing": "Finishing the pictures…",
        "changed": "Here is the changed picture.",
        "redrawn": "The stored drawing was too large to edit: the picture was drawn again from your first request with this change.",
        "changing": "Changing the picture…",
        "cpu_intro": "Here are {n} proposals.",
        "cpu_partial_one": "Only one of the two pictures could be made.",
        "cpu_photo": "These are illustrations, not photographs: no realistic photo is made here.",
        "cpu_drawing": "Drawing two proposals…",
        "cpu_drawing_done": "Drawing two proposals… {done} of 2 finished",
        "cpu_refused": "I can't draw this: the request asks for sexual, violent, cruel or hateful content. I can draw something else.",
        "cpu_filtered": "The pictures drawn for this request did not pass the content check, so none is shown. You can rephrase the request.",
        "cpu_check_one": "An automatic check found that this picture may not show {items}.",
        "cpu_check_all": "An automatic check found that these pictures may not show {items}.",
        "cpu_check_some": "An automatic check found that picture {n} may not show {items}.",
        "cpu_check_unknown": "The image model does not always follow exact counts and unusual details, and they could not be checked this time: {items}.",
        "cpu_variation": "I found nothing precise to change in the description, so this is a new draw of the same description (another random start), not a corrected picture. Tell me what should change and I will change exactly that.",
        "cpu_retrying": "Checking the pictures… drawing more ({done} so far)",
        "cpu_retried": "The image model often misses {items}: I made {n} attempts, checked each one, and show only those where the check finds it.",
        "cpu_unsatisfied_one": "The image model could not draw {items}: an automatic check finds it in none of my {n} attempts. This is the closest attempt, not what you asked for.",
        "cpu_unsatisfied_all": "The image model could not draw {items}: an automatic check finds it in none of my {n} attempts. These are the closest attempts, not what you asked for.",
        "code_instead": "The image model could not draw {items} (none of {n} attempts passed an automatic check): these pictures are drawn by code instead, and the same check finds it in them.",
        "cpu_again_checked": "The description of the picture already asks for {items}, and the image model did not follow it: this is a new draw of it with another random start, checked.",
        "cpu_again": "The description of the picture did not change with this request (it may already ask for it): this is a new draw of the same description with another random start, not a corrected picture.",
        "gap_seen": "(the picture shows: {seen})",
        "code_drawing": "Drawing it by code, where each part is counted…",
        "cpu_not_applied": "I could not turn this request into a change of the picture's description (the description came back unchanged), so nothing was drawn: version {version} stays as it is. Tell me the change in other words, for example what to add, remove or count.",
        "strategy_code": "The image model missed {items} again, so this time I changed method: this picture is drawn by code, where each part is drawn and counted one by one, and an automatic check finds it.",
        "code_failed_too": "A drawing by code did not pass the check either.",
        "cpu_kept": "My {n} new attempts miss {items}, by an automatic check, so none is shown: version {version}, which the check finds right, stays as it is.",
        "cpu_same_image": "The image model drew exactly the same picture again: nothing changed, and version {version} stays as it is. Tell me precisely what should change.",
        "same_drawing": "The drawing came back identical: nothing changed, and version {version} stays as it is. Tell me precisely what should change.",
        "code_retried": "I made {n} drawings and checked each one: this one shows {items}.",
        "code_unsatisfied": "The drawing by code still misses {items}: an automatic check finds it in none of my {n} attempts. This is the closest attempt, not what you asked for.",
        "code_check_unknown": "This drawing could not be checked this time: {items} is not verified.",
        "code_kept": "My {n} new drawings miss {items}, by an automatic check, so none is shown: version {version}, which the check finds right, stays as it is.",
        "redrawn_after_same": "Edited in place, the drawing came back identical, so I changed method: the picture was drawn again from your first request with this correction.",
        "same_drawing_again": "Nothing was redrawn: edited in place, then drawn again from your first request, the drawing came back identical both times, so version {version} stays as it is. Describe the part to correct another way (its place, size or colour) and I will try again.",
        "reviewing": "Looking at the picture…",
        "review_seen": "I looked at the current picture (version {version}).",
        "review_unseen": "I could not look at the picture itself this time: what follows only compares your requests with the description it was drawn from.",
        "review_shown": "It shows: {items}.",
        "review_missing": "Not shown, or wrong: {items}.",
        "review_other": "Other defects I see: {items}.",
        "review_plan": "What I propose to change: {items}.",
        "review_none": "I see nothing that differs from what you asked.",
        "review_asked": "What you asked for: {items}.",
        "review_next": "Say “go ahead” and I will redraw the picture with these corrections, or tell me what to change.",
        "review_method_cpu": "How changes work here: each change rewrites the picture's description and draws it again, so details the description does not fix can move.",
        "review_method_edit": "How changes work here: each change edits the current picture with the image model, and the rest is kept as it is.",
        "review_method_code": "How changes work here: each change asks the drawing model to edit its own drawing.",
    },
    "fr": {
        "intro": (
            "Voici {n} propositions ; la première, encadrée, est celle que je suggère."
        ),
        "single": "Voici l'image.",
        "partial_one": "Une seule des quatre esquisses a pu être terminée.",
        "partial": "Seules {n} des quatre esquisses ont pu être terminées.",
        "hint": (
            "Cliquez par exemple sur « Image 2 » pour la choisir et la voir en "
            "grand, ou répondez « la deuxième ». Sans choix, la première sert pour "
            "la suite."
        ),
        "links": "Choisir :",
        "label": "Image {n}",
        "sheet": "Propositions 1 à {n}",
        "photo": (
            "Il s'agit d'illustrations dessinées par du code, pas de photographies : "
            "aucune photo réaliste n'est faite ici."
        ),
        "chosen": "L'image {n} est choisie ; les modifications s'appliqueront à elle.",
        "missing": (
            "Les dernières propositions comptent {count} images : il n'y a pas "
            "d'image {n}."
        ),
        "missing_shown": "Cette conversation montre {count} images : il n'y a pas d'image {n}.",
        "cpu_no_edit_server": "Le serveur d'images utilisé ne sait pas retoucher une image existante : il ne peut qu'en dessiner une nouvelle.",
        "cpu_no_edit_busy": "Le serveur d'images est trop occupé pour retoucher l'image dans le temps de cette réponse.",
        "cpu_no_edit_failed": "Le serveur d'images n'a pas réussi à retoucher l'image cette fois.",
        "cpu_no_edit_original": "L'original de cette image n'est plus conservé : elle ne peut pas être retouchée.",
        "cpu_no_edit_offer": "Rien n'a donc été redessiné : la version {version} reste telle quelle. L'option la plus proche est de la redessiner à partir de sa description avec cette modification et le même point de départ : la composition reste proche, mais d'autres détails peuvent changer. Dites-moi « oui » pour le faire ; une retouche de l'image elle-même pourra être tentée de nouveau plus tard.",
        "cpu_redrawn": "Cette image n'a pas été retouchée sur place : comme proposé, elle a été redessinée à partir de sa description avec la modification, donc d'autres détails peuvent différer de la version précédente.",
        "expired": (
            "Ces propositions ne sont plus conservées (conservation de 30 jours)."
        ),
        "reading": "Lecture de la demande…",
        "drawing": "Dessin de quatre propositions…",
        "drawing_done": "Dessin de quatre propositions… {done} sur 4 terminées",
        "finishing": "Finition des images…",
        "changed": "Voici l'image modifiée.",
        "redrawn": "Le dessin conservé était trop volumineux pour être retouché : l'image a été redessinée à partir de votre première demande avec cette modification.",
        "changing": "Modification de l'image…",
        "cpu_intro": "Voici {n} propositions.",
        "cpu_partial_one": "Une seule des deux images a pu être faite.",
        "cpu_photo": "Il s'agit d'illustrations, pas de photographies : aucune photo réaliste n'est faite ici.",
        "cpu_drawing": "Dessin de deux propositions…",
        "cpu_drawing_done": "Dessin de deux propositions… {done} sur 2 terminées",
        "cpu_refused": "Je ne peux pas dessiner cela : la demande porte sur un contenu sexuel, violent, cruel ou haineux. Je peux dessiner autre chose.",
        "cpu_filtered": "Les images dessinées pour cette demande n'ont pas passé le contrôle de contenu : aucune n'est montrée. Vous pouvez reformuler la demande.",
        "cpu_check_one": "Vérification automatique : cette image ne montre peut-être pas {items}.",
        "cpu_check_all": "Vérification automatique : ces images ne montrent peut-être pas {items}.",
        "cpu_check_some": "Vérification automatique : l'image {n} ne montre peut-être pas {items}.",
        "cpu_check_unknown": "Le modèle d'images ne respecte pas toujours les nombres exacts et les détails inhabituels, et ils n'ont pas pu être vérifiés cette fois : {items}.",
        "cpu_variation": "Je n'ai trouvé aucune correction précise à apporter à la description : voici donc un nouveau tirage de la même description (un autre départ aléatoire), pas une image corrigée. Dites-moi ce qui doit changer et je changerai exactement cela.",
        "cpu_retrying": "Vérification des images… nouveaux essais ({done} jusqu'ici)",
        "cpu_retried": "Le modèle d'images rate souvent {items} : j'ai fait {n} essais, vérifié chacun, et je ne montre que ceux où la vérification le trouve.",
        "cpu_unsatisfied_one": "Le modèle d'images n'a pas réussi à dessiner {items} : une vérification automatique ne le trouve dans aucun de mes {n} essais. Voici l'essai le plus proche, qui n'est pas ce que vous avez demandé.",
        "cpu_unsatisfied_all": "Le modèle d'images n'a pas réussi à dessiner {items} : une vérification automatique ne le trouve dans aucun de mes {n} essais. Voici les essais les plus proches, qui ne sont pas ce que vous avez demandé.",
        "code_instead": "Le modèle d'images n'a pas réussi à dessiner {items} (aucun de ses {n} essais n'a passé la vérification automatique) : ces images sont dessinées par du code à la place, et la même vérification les trouve conformes.",
        "cpu_again_checked": "La description de l'image demande déjà {items}, et le modèle d'images ne l'a pas respectée : voici donc un nouveau tirage, avec un autre départ aléatoire, vérifié.",
        "cpu_again": "La description de l'image n'a pas changé avec cette demande (elle la contient peut-être déjà) : voici un nouveau tirage de la même description, avec un autre départ aléatoire, pas une image corrigée.",
        "gap_seen": "(l'image montre : {seen})",
        "code_drawing": "Dessin par du code, chaque partie comptée…",
        "cpu_not_applied": "Je n'ai pas réussi à traduire cette demande en un changement de la description de l'image (la description est revenue identique) : rien n'a été redessiné, la version {version} reste telle quelle. Dites-moi le changement autrement, par exemple ce qu'il faut ajouter, enlever ou compter.",
        "strategy_code": "Le modèle d'images a encore manqué {items} : cette fois, j'ai changé de méthode. Cette image est dessinée par du code, qui dessine et compte chaque partie une à une, et une vérification automatique la trouve conforme.",
        "code_failed_too": "Un dessin par du code n'a pas passé la vérification non plus.",
        "cpu_kept": "Mes {n} nouveaux essais ne montrent pas {items}, d'après une vérification automatique : aucun n'est montré, et la version {version}, que la vérification trouve conforme, reste telle quelle.",
        "cpu_same_image": "Le modèle d'images a redessiné une image identique : rien n'a changé, la version {version} reste telle quelle. Dites-moi précisément ce qui doit changer.",
        "same_drawing": "Le dessin est revenu identique : rien n'a changé, la version {version} reste telle quelle. Dites-moi précisément ce qui doit changer.",
        "code_retried": "J'ai fait {n} dessins et vérifié chacun : celui-ci montre {items}.",
        "code_unsatisfied": "Le dessin par code n'a pas réussi à montrer {items} : une vérification automatique ne le trouve dans aucun de mes {n} essais. Voici l'essai le plus proche, qui n'est pas ce que vous avez demandé.",
        "code_check_unknown": "Ce dessin n'a pas pu être vérifié cette fois : {items} n'est pas vérifié.",
        "code_kept": "Mes {n} nouveaux dessins ne montrent pas {items}, d'après une vérification automatique : aucun n'est montré, et la version {version}, que la vérification trouve conforme, reste telle quelle.",
        "redrawn_after_same": "Retouché sur place, le dessin revenait identique : j'ai donc changé de méthode, et l'image a été redessinée à partir de votre première demande avec cette correction.",
        "same_drawing_again": "Rien n'a été redessiné : retouché, puis redessiné à partir de votre première demande, le dessin est revenu identique les deux fois, donc la version {version} reste telle quelle. Décrivez autrement l'élément à corriger (sa place, sa taille ou sa couleur) et je réessaierai.",
        "reviewing": "Examen de l'image…",
        "review_seen": "J'ai regardé l'image actuelle (version {version}).",
        "review_unseen": "Je n'ai pas pu regarder l'image elle-même cette fois : ce qui suit compare seulement vos demandes avec la description d'après laquelle elle a été dessinée.",
        "review_shown": "Elle montre : {items}.",
        "review_missing": "Absent ou faux : {items}.",
        "review_other": "Autres défauts visibles : {items}.",
        "review_plan": "Ce que je propose de changer : {items}.",
        "review_none": "Je ne vois rien qui diffère de ce que vous avez demandé.",
        "review_asked": "Ce que vous avez demandé : {items}.",
        "review_next": "Dites « vas-y » et je redessinerai l'image avec ces corrections, ou dites-moi ce qu'il faut changer.",
        "review_method_cpu": "Comment fonctionnent les modifications ici : chaque modification réécrit la description de l'image et la redessine, donc les détails que la description ne fixe pas peuvent bouger.",
        "review_method_edit": "Comment fonctionnent les modifications ici : chaque modification retouche l'image actuelle avec le modèle d'images, et le reste est conservé.",
        "review_method_code": "Comment fonctionnent les modifications ici : chaque modification demande au modèle de dessin de retoucher son propre dessin.",
    },
    "de": {
        "intro": (
            "Hier sind {n} Vorschläge; der erste, umrahmt, ist der, den ich empfehle."
        ),
        "single": "Hier ist das Bild.",
        "partial_one": "Nur einer der vier Entwürfe konnte fertiggestellt werden.",
        "partial": "Nur {n} der vier Entwürfe konnten fertiggestellt werden.",
        "hint": (
            "Klicken Sie zum Beispiel auf „Bild 2“, um es zu wählen und groß zu "
            "sehen, oder antworten Sie „das zweite“. Ohne Wahl gilt das erste für "
            "weitere Änderungen."
        ),
        "links": "Wählen:",
        "label": "Bild {n}",
        "sheet": "Vorschläge 1 bis {n}",
        "photo": (
            "Das sind mit Code gezeichnete Illustrationen, keine Fotos: realistische "
            "Fotos entstehen hier nicht."
        ),
        "chosen": "Bild {n} ist gewählt; Änderungen gelten für dieses Bild.",
        "missing": "Die letzten Vorschläge haben {count} Bilder: es gibt kein Bild {n}.",
        "missing_shown": "Dieses Gespräch zeigt {count} Bilder: es gibt kein Bild {n}.",
        "cpu_no_edit_server": "Der verwendete Bildserver kann ein vorhandenes Bild nicht bearbeiten: Er kann nur ein neues zeichnen.",
        "cpu_no_edit_busy": "Der Bildserver ist gerade zu ausgelastet, um das Bild in der Zeit dieser Antwort zu bearbeiten.",
        "cpu_no_edit_failed": "Der Bildserver konnte das Bild diesmal nicht bearbeiten.",
        "cpu_no_edit_original": "Das Original dieses Bildes ist nicht mehr gespeichert, daher kann es nicht bearbeitet werden.",
        "cpu_no_edit_offer": "Es wurde also nichts neu gezeichnet: Version {version} bleibt, wie sie ist. Die nächstliegende Möglichkeit ist, es aus seiner Beschreibung mit dieser Änderung und demselben Ausgangspunkt neu zu zeichnen: Die Komposition bleibt ähnlich, andere Details können sich aber ändern. Sagen Sie mir „ja“, dann zeichne ich es so; eine Bearbeitung des Bildes selbst kann später erneut versucht werden.",
        "cpu_redrawn": "Dieses Bild wurde nicht direkt bearbeitet: Wie vorgeschlagen wurde es aus seiner Beschreibung mit der Änderung neu gezeichnet, daher können andere Details von der vorherigen Version abweichen.",
        "expired": (
            "Diese Vorschläge sind nicht mehr gespeichert (Aufbewahrung 30 Tage)."
        ),
        "reading": "Anfrage wird gelesen…",
        "drawing": "Vier Vorschläge werden gezeichnet…",
        "drawing_done": "Vier Vorschläge werden gezeichnet… {done} von 4 fertig",
        "finishing": "Bilder werden fertiggestellt…",
        "changed": "Hier ist das geänderte Bild.",
        "redrawn": "Die gespeicherte Zeichnung war zu groß zum Bearbeiten: Das Bild wurde aus Ihrer ersten Anfrage mit dieser Änderung neu gezeichnet.",
        "changing": "Bild wird geändert…",
        "cpu_intro": "Hier sind {n} Vorschläge.",
        "cpu_partial_one": "Nur eines der zwei Bilder konnte erstellt werden.",
        "cpu_photo": "Das sind Illustrationen, keine Fotos: realistische Fotos entstehen hier nicht.",
        "cpu_drawing": "Zwei Vorschläge werden gezeichnet…",
        "cpu_drawing_done": "Zwei Vorschläge werden gezeichnet… {done} von 2 fertig",
        "cpu_refused": "Das kann ich nicht zeichnen: Die Anfrage verlangt sexuelle, gewalttätige, grausame oder hasserfüllte Inhalte. Ich kann gern etwas anderes zeichnen.",
        "cpu_filtered": "Die für diese Anfrage gezeichneten Bilder haben die Inhaltsprüfung nicht bestanden, daher wird keines gezeigt. Sie können die Anfrage umformulieren.",
        "cpu_check_one": "Automatische Prüfung: Dieses Bild zeigt möglicherweise nicht {items}.",
        "cpu_check_all": "Automatische Prüfung: Diese Bilder zeigen möglicherweise nicht {items}.",
        "cpu_check_some": "Automatische Prüfung: Bild {n} zeigt möglicherweise nicht {items}.",
        "cpu_check_unknown": "Das Bildmodell hält genaue Anzahlen und ungewöhnliche Details nicht immer ein, und sie konnten diesmal nicht geprüft werden: {items}.",
        "cpu_variation": "Ich habe nichts Genaues gefunden, das an der Beschreibung zu ändern wäre: Das ist daher ein neuer Entwurf derselben Beschreibung (ein anderer Zufallsstart), kein korrigiertes Bild. Sagen Sie mir, was sich ändern soll, dann ändere ich genau das.",
        "cpu_retrying": "Bilder werden geprüft… weitere Versuche ({done} bisher)",
        "cpu_retried": "Das Bildmodell verfehlt oft {items}: Ich habe {n} Versuche gemacht, jeden geprüft, und zeige nur die, in denen die Prüfung es findet.",
        "cpu_unsatisfied_one": "Das Bildmodell konnte {items} nicht zeichnen: Eine automatische Prüfung findet es in keinem meiner {n} Versuche. Das ist der nächstliegende Versuch, nicht das, was Sie wollten.",
        "cpu_unsatisfied_all": "Das Bildmodell konnte {items} nicht zeichnen: Eine automatische Prüfung findet es in keinem meiner {n} Versuche. Das sind die nächstliegenden Versuche, nicht das, was Sie wollten.",
        "code_instead": "Das Bildmodell konnte {items} nicht zeichnen (keiner seiner {n} Versuche hat die automatische Prüfung bestanden): Diese Bilder sind stattdessen mit Code gezeichnet, und dieselbe Prüfung findet es darin.",
        "cpu_again_checked": "Die Bildbeschreibung verlangt bereits {items}, und das Bildmodell hat sich nicht daran gehalten: Das ist daher ein neuer Entwurf mit einem anderen Zufallsstart, geprüft.",
        "cpu_again": "Die Bildbeschreibung hat sich mit dieser Anfrage nicht geändert (vielleicht enthält sie sie schon): Das ist ein neuer Entwurf derselben Beschreibung mit einem anderen Zufallsstart, kein korrigiertes Bild.",
        "gap_seen": "(das Bild zeigt: {seen})",
        "code_drawing": "Zeichnung mit Code, jedes Teil gezählt…",
        "cpu_not_applied": "Ich konnte diese Anfrage nicht in eine Änderung der Bildbeschreibung übersetzen (die Beschreibung kam unverändert zurück): Es wurde nichts neu gezeichnet, Version {version} bleibt, wie sie ist. Sagen Sie mir die Änderung mit anderen Worten, zum Beispiel was hinzukommen, wegfallen oder gezählt werden soll.",
        "strategy_code": "Das Bildmodell hat {items} erneut verfehlt, daher habe ich diesmal die Methode gewechselt: Dieses Bild ist mit Code gezeichnet, der jedes Teil einzeln zeichnet und zählt, und eine automatische Prüfung findet es darin.",
        "code_failed_too": "Eine Zeichnung mit Code hat die Prüfung ebenfalls nicht bestanden.",
        "cpu_kept": "Meine {n} neuen Versuche zeigen {items} nicht, laut einer automatischen Prüfung: Keiner wird gezeigt, und Version {version}, die die Prüfung für richtig hält, bleibt, wie sie ist.",
        "cpu_same_image": "Das Bildmodell hat genau dasselbe Bild noch einmal gezeichnet: Nichts hat sich geändert, Version {version} bleibt, wie sie ist. Sagen Sie mir genau, was sich ändern soll.",
        "same_drawing": "Die Zeichnung kam unverändert zurück: Nichts hat sich geändert, Version {version} bleibt, wie sie ist. Sagen Sie mir genau, was sich ändern soll.",
        "code_retried": "Ich habe {n} Zeichnungen gemacht und jede geprüft: Diese zeigt {items}.",
        "code_unsatisfied": "Die Zeichnung per Code zeigt {items} nach einer automatischen Prüfung in keinem meiner {n} Versuche. Dies ist der nächste Versuch, nicht das, was Sie wollten.",
        "code_check_unknown": "Diese Zeichnung konnte diesmal nicht geprüft werden: {items} ist nicht geprüft.",
        "code_kept": "Meine {n} neuen Zeichnungen zeigen {items} laut einer automatischen Prüfung nicht, daher wird keine gezeigt: Version {version}, die die Prüfung für richtig hält, bleibt, wie sie ist.",
        "redrawn_after_same": "Direkt bearbeitet kam die Zeichnung unverändert zurück, deshalb habe ich die Methode gewechselt: Das Bild wurde aus Ihrer ersten Anfrage mit dieser Korrektur neu gezeichnet.",
        "same_drawing_again": "Nichts wurde neu gezeichnet: Bearbeitet und dann aus Ihrer ersten Anfrage neu gezeichnet, kam die Zeichnung beide Male unverändert zurück; Version {version} bleibt, wie sie ist. Beschreiben Sie den zu korrigierenden Teil anders (Lage, Größe oder Farbe), dann versuche ich es erneut.",
        "reviewing": "Bild wird angesehen…",
        "review_seen": "Ich habe mir das aktuelle Bild angesehen (Version {version}).",
        "review_unseen": "Ich konnte mir das Bild selbst diesmal nicht ansehen: Das Folgende vergleicht nur Ihre Wünsche mit der Beschreibung, nach der es gezeichnet wurde.",
        "review_shown": "Es zeigt: {items}.",
        "review_missing": "Fehlt oder ist falsch: {items}.",
        "review_other": "Weitere sichtbare Fehler: {items}.",
        "review_plan": "Was ich ändern würde: {items}.",
        "review_none": "Ich sehe nichts, was von Ihren Wünschen abweicht.",
        "review_asked": "Was Sie sich gewünscht haben: {items}.",
        "review_next": "Sagen Sie „mach weiter“, dann zeichne ich das Bild mit diesen Korrekturen neu, oder sagen Sie mir, was sich ändern soll.",
        "review_method_cpu": "So funktionieren Änderungen hier: Jede Änderung schreibt die Bildbeschreibung neu und zeichnet das Bild neu, daher können Details, die die Beschreibung nicht festlegt, sich verschieben.",
        "review_method_edit": "So funktionieren Änderungen hier: Jede Änderung bearbeitet das aktuelle Bild mit dem Bildmodell, der Rest bleibt erhalten.",
        "review_method_code": "So funktionieren Änderungen hier: Jede Änderung lässt das Zeichenmodell seine eigene Zeichnung bearbeiten.",
    },
    "es": {
        "intro": "Aquí tienes {n} propuestas; la primera, enmarcada, es la que sugiero.",
        "single": "Aquí está la imagen.",
        "partial_one": "Solo se pudo terminar uno de los cuatro bocetos.",
        "partial": "Solo se pudieron terminar {n} de los cuatro bocetos.",
        "hint": (
            "Haz clic por ejemplo en «Imagen 2» para elegirla y verla en grande, o "
            "responde «la segunda». Sin elección, la primera se usa para los "
            "cambios."
        ),
        "links": "Elegir:",
        "label": "Imagen {n}",
        "sheet": "Propuestas 1 a {n}",
        "photo": (
            "Son ilustraciones dibujadas con código, no fotografías: aquí no se "
            "hacen fotos realistas."
        ),
        "chosen": "La imagen {n} está elegida; los cambios se aplicarán a ella.",
        "missing": (
            "Las últimas propuestas tienen {count} imágenes: no hay imagen {n}."
        ),
        "missing_shown": "Esta conversación muestra {count} imágenes: no hay imagen {n}.",
        "cpu_no_edit_server": "El servidor de imágenes en uso no sabe editar una imagen existente: solo puede dibujar una nueva.",
        "cpu_no_edit_busy": "El servidor de imágenes está demasiado ocupado para editar la imagen en el tiempo de esta respuesta.",
        "cpu_no_edit_failed": "El servidor de imágenes no consiguió editar la imagen esta vez.",
        "cpu_no_edit_original": "El original de esta imagen ya no está guardado, así que no se puede editar.",
        "cpu_no_edit_offer": "Así que no se ha redibujado nada: la versión {version} se queda como está. La opción más cercana es dibujarla de nuevo a partir de su descripción con este cambio y el mismo punto de partida: la composición queda parecida, pero otros detalles pueden cambiar. Dígame «sí» para hacerlo; una edición de la imagen misma se puede intentar de nuevo más tarde.",
        "cpu_redrawn": "Esta imagen no se editó directamente: como se propuso, se dibujó de nuevo a partir de su descripción con el cambio, así que otros detalles pueden diferir de la versión anterior.",
        "expired": "Estas propuestas ya no se conservan (conservación de 30 días).",
        "reading": "Leyendo la petición…",
        "drawing": "Dibujando cuatro propuestas…",
        "drawing_done": "Dibujando cuatro propuestas… {done} de 4 terminadas",
        "finishing": "Terminando las imágenes…",
        "changed": "Aquí está la imagen modificada.",
        "redrawn": "El dibujo guardado era demasiado grande para retocarlo: la imagen se ha vuelto a dibujar a partir de su primera petición con este cambio.",
        "changing": "Modificando la imagen…",
        "cpu_intro": "Aquí tienes {n} propuestas.",
        "cpu_partial_one": "Solo se pudo hacer una de las dos imágenes.",
        "cpu_photo": "Son ilustraciones, no fotografías: aquí no se hacen fotos realistas.",
        "cpu_drawing": "Dibujando dos propuestas…",
        "cpu_drawing_done": "Dibujando dos propuestas… {done} de 2 terminadas",
        "cpu_refused": "No puedo dibujar esto: la petición pide contenido sexual, violento, cruel u odioso. Puedo dibujar otra cosa.",
        "cpu_filtered": "Las imágenes dibujadas para esta petición no pasaron el control de contenido, así que no se muestra ninguna. Puedes reformular la petición.",
        "cpu_check_one": "Comprobación automática: puede que esta imagen no muestre {items}.",
        "cpu_check_all": "Comprobación automática: puede que estas imágenes no muestren {items}.",
        "cpu_check_some": "Comprobación automática: puede que la imagen {n} no muestre {items}.",
        "cpu_check_unknown": "El modelo de imágenes no siempre respeta los números exactos ni los detalles inusuales, y esta vez no se pudieron comprobar: {items}.",
        "cpu_variation": "No encontré nada concreto que cambiar en la descripción, así que esta es una nueva versión de la misma descripción (otro inicio aleatorio), no una imagen corregida. Dígame qué debe cambiar y cambiaré exactamente eso.",
        "cpu_retrying": "Comprobando las imágenes… más intentos ({done} hasta ahora)",
        "cpu_retried": "El modelo de imágenes suele fallar en {items}: hice {n} intentos, comprobé cada uno y solo muestro los que pasan la comprobación.",
        "cpu_unsatisfied_one": "El modelo de imágenes no consiguió dibujar {items}: una comprobación automática no lo encuentra en ninguno de mis {n} intentos. Este es el intento más cercano, no lo que pidió.",
        "cpu_unsatisfied_all": "El modelo de imágenes no consiguió dibujar {items}: una comprobación automática no lo encuentra en ninguno de mis {n} intentos. Estos son los intentos más cercanos, no lo que pidió.",
        "code_instead": "El modelo de imágenes no consiguió dibujar {items} (ninguno de sus {n} intentos pasó la comprobación automática): estas imágenes están dibujadas con código y la misma comprobación lo encuentra en ellas.",
        "cpu_again_checked": "La descripción de la imagen ya pide {items}, y el modelo de imágenes no la respetó: esta es una nueva versión con otro inicio aleatorio, comprobada.",
        "cpu_again": "La descripción de la imagen no cambió con esta petición (quizá ya lo contiene): esta es una nueva versión de la misma descripción con otro inicio aleatorio, no una imagen corregida.",
        "gap_seen": "(la imagen muestra: {seen})",
        "code_drawing": "Dibujando con código, cada parte contada…",
        "cpu_not_applied": "No conseguí convertir esta petición en un cambio de la descripción de la imagen (la descripción volvió igual): no se ha dibujado nada y la versión {version} se queda como está. Dígame el cambio con otras palabras, por ejemplo qué añadir, quitar o contar.",
        "strategy_code": "El modelo de imágenes volvió a fallar en {items}, así que esta vez cambié de método: esta imagen está dibujada con código, que dibuja y cuenta cada parte una a una, y una comprobación automática lo encuentra en ella.",
        "code_failed_too": "Un dibujo con código tampoco pasó la comprobación.",
        "cpu_kept": "Mis {n} nuevos intentos no muestran {items}, según una comprobación automática: no se muestra ninguno y la versión {version}, que la comprobación encuentra correcta, se queda como está.",
        "cpu_same_image": "El modelo de imágenes volvió a dibujar exactamente la misma imagen: no ha cambiado nada y la versión {version} se queda como está. Dígame con precisión qué debe cambiar.",
        "same_drawing": "El dibujo volvió idéntico: no ha cambiado nada y la versión {version} se queda como está. Dígame con precisión qué debe cambiar.",
        "code_retried": "Hice {n} dibujos y comprobé cada uno: este muestra {items}.",
        "code_unsatisfied": "El dibujo por código no ha conseguido mostrar {items}: una comprobación automática no lo encuentra en ninguno de mis {n} intentos. Este es el intento más cercano, no lo que usted pidió.",
        "code_check_unknown": "Este dibujo no se ha podido comprobar esta vez: {items} no está comprobado.",
        "code_kept": "Mis {n} nuevos dibujos no muestran {items}, según una comprobación automática, así que no se muestra ninguno: la versión {version}, que la comprobación considera correcta, se queda como está.",
        "redrawn_after_same": "Retocado directamente, el dibujo volvía idéntico, así que cambié de método: la imagen se ha vuelto a dibujar a partir de su primera petición con esta corrección.",
        "same_drawing_again": "No se ha redibujado nada: retocado y luego redibujado a partir de su primera petición, el dibujo volvió idéntico las dos veces, así que la versión {version} se queda como está. Describa de otra forma la parte que hay que corregir (su lugar, tamaño o color) y lo intentaré de nuevo.",
        "reviewing": "Revisando la imagen…",
        "review_seen": "He mirado la imagen actual (versión {version}).",
        "review_unseen": "Esta vez no pude mirar la imagen en sí: lo que sigue solo compara sus peticiones con la descripción a partir de la cual se dibujó.",
        "review_shown": "Muestra: {items}.",
        "review_missing": "Falta o está mal: {items}.",
        "review_other": "Otros defectos visibles: {items}.",
        "review_plan": "Lo que propongo cambiar: {items}.",
        "review_none": "No veo nada que difiera de lo que pidió.",
        "review_asked": "Lo que pidió: {items}.",
        "review_next": "Diga «adelante» y volveré a dibujar la imagen con estas correcciones, o dígame qué cambiar.",
        "review_method_cpu": "Cómo funcionan los cambios aquí: cada cambio reescribe la descripción de la imagen y la vuelve a dibujar, así que los detalles que la descripción no fija pueden moverse.",
        "review_method_edit": "Cómo funcionan los cambios aquí: cada cambio retoca la imagen actual con el modelo de imágenes y el resto se conserva.",
        "review_method_code": "Cómo funcionan los cambios aquí: cada cambio pide al modelo de dibujo que retoque su propio dibujo.",
    },
    "it": {
        "intro": "Ecco {n} proposte; la prima, incorniciata, è quella che suggerisco.",
        "single": "Ecco l'immagine.",
        "partial_one": "È stato possibile completare solo uno dei quattro bozzetti.",
        "partial": "È stato possibile completare solo {n} dei quattro bozzetti.",
        "hint": (
            "Fai clic per esempio su «Immagine 2» per sceglierla e vederla in "
            "grande, oppure rispondi «la seconda». Senza scelta, la prima vale per "
            "le modifiche."
        ),
        "links": "Scegli:",
        "label": "Immagine {n}",
        "sheet": "Proposte da 1 a {n}",
        "photo": (
            "Sono illustrazioni disegnate dal codice, non fotografie: qui non si "
            "fanno foto realistiche."
        ),
        "chosen": "L'immagine {n} è scelta; le modifiche si applicheranno a essa.",
        "missing": (
            "Le ultime proposte hanno {count} immagini: non c'è l'immagine {n}."
        ),
        "missing_shown": "Questa conversazione mostra {count} immagini: non c'è l'immagine {n}.",
        "cpu_no_edit_server": "Il server di immagini in uso non sa modificare un'immagine esistente: può solo disegnarne una nuova.",
        "cpu_no_edit_busy": "Il server di immagini è troppo occupato per modificare l'immagine nel tempo di questa risposta.",
        "cpu_no_edit_failed": "Il server di immagini non è riuscito a modificare l'immagine questa volta.",
        "cpu_no_edit_original": "L'originale di questa immagine non è più conservato, quindi non può essere modificata.",
        "cpu_no_edit_offer": "Quindi non è stato ridisegnato nulla: la versione {version} resta com'è. L'opzione più vicina è ridisegnarla dalla sua descrizione con questa modifica e lo stesso punto di partenza: la composizione resta simile, ma altri dettagli possono cambiare. Dimmi «sì» per farlo; una modifica dell'immagine stessa si può riprovare più tardi.",
        "cpu_redrawn": "Questa immagine non è stata modificata direttamente: come proposto, è stata ridisegnata dalla sua descrizione con la modifica, quindi altri dettagli possono differire dalla versione precedente.",
        "expired": "Queste proposte non sono più conservate (conservazione di 30 giorni).",
        "reading": "Lettura della richiesta…",
        "drawing": "Disegno di quattro proposte…",
        "drawing_done": "Disegno di quattro proposte… {done} su 4 completate",
        "finishing": "Rifinitura delle immagini…",
        "changed": "Ecco l'immagine modificata.",
        "redrawn": "Il disegno salvato era troppo grande da ritoccare: l'immagine è stata ridisegnata dalla prima richiesta con questa modifica.",
        "changing": "Modifica dell'immagine…",
        "cpu_intro": "Ecco {n} proposte.",
        "cpu_partial_one": "È stato possibile fare solo una delle due immagini.",
        "cpu_photo": "Sono illustrazioni, non fotografie: qui non si fanno foto realistiche.",
        "cpu_drawing": "Disegno di due proposte…",
        "cpu_drawing_done": "Disegno di due proposte… {done} su 2 completate",
        "cpu_refused": "Non posso disegnarlo: la richiesta chiede contenuti sessuali, violenti, crudeli o d'odio. Posso disegnare altro.",
        "cpu_filtered": "Le immagini disegnate per questa richiesta non hanno superato il controllo dei contenuti, quindi non ne viene mostrata nessuna. Puoi riformulare la richiesta.",
        "cpu_check_one": "Controllo automatico: questa immagine potrebbe non mostrare {items}.",
        "cpu_check_all": "Controllo automatico: queste immagini potrebbero non mostrare {items}.",
        "cpu_check_some": "Controllo automatico: l'immagine {n} potrebbe non mostrare {items}.",
        "cpu_check_unknown": "Il modello di immagini non rispetta sempre i numeri esatti e i dettagli insoliti, e questa volta non è stato possibile verificarli: {items}.",
        "cpu_variation": "Non ho trovato nulla di preciso da cambiare nella descrizione: questa è quindi una nuova estrazione della stessa descrizione (un altro avvio casuale), non un'immagine corretta. Dimmi cosa deve cambiare e cambierò esattamente quello.",
        "cpu_retrying": "Controllo delle immagini… altri tentativi ({done} finora)",
        "cpu_retried": "Il modello di immagini sbaglia spesso {items}: ho fatto {n} tentativi, li ho controllati uno per uno e mostro solo quelli che superano il controllo.",
        "cpu_unsatisfied_one": "Il modello di immagini non è riuscito a disegnare {items}: un controllo automatico non lo trova in nessuno dei miei {n} tentativi. Questo è il tentativo più vicino, non quello che hai chiesto.",
        "cpu_unsatisfied_all": "Il modello di immagini non è riuscito a disegnare {items}: un controllo automatico non lo trova in nessuno dei miei {n} tentativi. Questi sono i tentativi più vicini, non quello che hai chiesto.",
        "code_instead": "Il modello di immagini non è riuscito a disegnare {items} (nessuno dei suoi {n} tentativi ha superato il controllo automatico): queste immagini sono disegnate dal codice, e lo stesso controllo lo trova in esse.",
        "cpu_again_checked": "La descrizione dell'immagine chiede già {items}, e il modello di immagini non l'ha rispettata: questa è quindi una nuova estrazione con un altro avvio casuale, controllata.",
        "cpu_again": "La descrizione dell'immagine non è cambiata con questa richiesta (forse la contiene già): questa è una nuova estrazione della stessa descrizione con un altro avvio casuale, non un'immagine corretta.",
        "gap_seen": "(l'immagine mostra: {seen})",
        "code_drawing": "Disegno con il codice, ogni parte contata…",
        "cpu_not_applied": "Non sono riuscito a tradurre questa richiesta in una modifica della descrizione dell'immagine (la descrizione è tornata identica): non è stato ridisegnato nulla e la versione {version} resta com'è. Dimmi la modifica con altre parole, per esempio cosa aggiungere, togliere o contare.",
        "strategy_code": "Il modello di immagini ha mancato di nuovo {items}, quindi questa volta ho cambiato metodo: questa immagine è disegnata dal codice, che disegna e conta ogni parte una per una, e un controllo automatico la trova conforme.",
        "code_failed_too": "Neanche un disegno con il codice ha superato il controllo.",
        "cpu_kept": "I miei {n} nuovi tentativi non mostrano {items}, secondo un controllo automatico: non ne mostro nessuno, e la versione {version}, che il controllo trova corretta, resta com'è.",
        "cpu_same_image": "Il modello di immagini ha ridisegnato esattamente la stessa immagine: non è cambiato nulla e la versione {version} resta com'è. Dimmi con precisione cosa deve cambiare.",
        "same_drawing": "Il disegno è tornato identico: non è cambiato nulla e la versione {version} resta com'è. Dimmi con precisione cosa deve cambiare.",
        "code_retried": "Ho fatto {n} disegni e li ho controllati uno per uno: questo mostra {items}.",
        "code_unsatisfied": "Il disegno con il codice non è riuscito a mostrare {items}: un controllo automatico non lo trova in nessuno dei miei {n} tentativi. Questo è il tentativo più vicino, non quello che hai chiesto.",
        "code_check_unknown": "Questo disegno non è stato controllato questa volta: {items} non è verificato.",
        "code_kept": "I miei {n} nuovi disegni non mostrano {items}, secondo un controllo automatico, quindi non ne mostro nessuno: la versione {version}, che il controllo trova corretta, resta com'è.",
        "redrawn_after_same": "Ritoccato direttamente, il disegno tornava identico, quindi ho cambiato metodo: l'immagine è stata ridisegnata dalla prima richiesta con questa correzione.",
        "same_drawing_again": "Non è stato ridisegnato nulla: ritoccato e poi ridisegnato dalla prima richiesta, il disegno è tornato identico entrambe le volte, quindi la versione {version} resta com'è. Descrivi in un altro modo la parte da correggere (posizione, dimensione o colore) e riproverò.",
        "reviewing": "Esame dell'immagine…",
        "review_seen": "Ho guardato l'immagine attuale (versione {version}).",
        "review_unseen": "Questa volta non ho potuto guardare l'immagine stessa: quanto segue confronta solo le tue richieste con la descrizione da cui è stata disegnata.",
        "review_shown": "Mostra: {items}.",
        "review_missing": "Manca o è sbagliato: {items}.",
        "review_other": "Altri difetti visibili: {items}.",
        "review_plan": "Cosa propongo di cambiare: {items}.",
        "review_none": "Non vedo nulla che differisca da quanto hai chiesto.",
        "review_asked": "Cosa hai chiesto: {items}.",
        "review_next": "Di' «vai» e ridisegnerò l'immagine con queste correzioni, oppure dimmi cosa cambiare.",
        "review_method_cpu": "Come funzionano le modifiche qui: ogni modifica riscrive la descrizione dell'immagine e la ridisegna, quindi i dettagli che la descrizione non fissa possono spostarsi.",
        "review_method_edit": "Come funzionano le modifiche qui: ogni modifica ritocca l'immagine attuale con il modello di immagini e il resto viene conservato.",
        "review_method_code": "Come funzionano le modifiche qui: ogni modifica chiede al modello di disegno di ritoccare il proprio disegno.",
    },
}


def language_code(value: object) -> str:
    """A language code as a model reply gives it ("es", "ES", "es-ES",
    "pt_BR"), reduced to its lower-case language part; "" when it is not one."""
    if not isinstance(value, str):
        return ""
    code = re.split(r"[-_]", value.strip().lower(), maxsplit=1)[0]
    return code if re.fullmatch(r"[a-z]{2,3}", code) else ""


def answer_language(found: object, fallback: str) -> str:
    """The language of the picture turn's fixed texts.

    The request's own language, as the turn's model call identified it, when it
    is one of the five: a short request carries too little prose for the
    conversation's language detection ("dibujame un gato con sombrero" with an
    English interface was answered in English, nightly 2026-11-15). Otherwise
    the conversation's language (`fallback`), and English for any other one.
    """
    code = language_code(found)
    if code in LANGUAGES:
        return code
    return fallback if fallback in LANGUAGES else "en"


# The polite phrases of these tables and their familiar form (longest first
# is the shared rule); packages.addressing.FAMILIAR adds the common ones.
FAMILIAR: dict[str, tuple[tuple[str, str], ...]] = {
    "fr": (
        ("cliquez", "clique"),
        ("répondez", "réponds"),
        ("vous pouvez", "tu peux"),
        ("votre première demande", "ta première demande"),
        ("dites-moi", "dis-moi"),
        ("dites", "dis"),
        ("ce que vous avez demandé", "ce que tu as demandé"),
        ("vos demandes", "tes demandes"),
        ("dites-moi le changement", "dis-moi le changement"),
        ("décrivez", "décris"),
    ),
    "de": (
        ("Klicken Sie", "klick"),
        ("antworten Sie", "antworte"),
        ("Ihrer ersten Anfrage", "deiner ersten Anfrage"),
        ("Sie können", "du kannst"),
        ("Sagen Sie mir", "sag mir"),
        ("sagen Sie mir", "sag mir"),
        ("Sagen Sie", "sag"),
        ("Ihren Wünschen", "deinen Wünschen"),
        ("Ihre Wünsche", "deine Wünsche"),
        ("Was Sie sich gewünscht haben", "was du dir gewünscht hast"),
        ("was Sie wollten", "was du wolltest"),
        ("Sagen Sie mir die Änderung", "sag mir die Änderung"),
        ("Beschreiben Sie", "beschreib"),
    ),
    "es": (
        ("dígame", "dime"),
        ("diga", "di"),
        ("su primera petición", "tu primera petición"),
        ("sus peticiones", "tus peticiones"),
        ("describa", "describe"),
        ("lo que usted pidió", "lo que pediste"),
    ),
}


def text(language: str, key: str, **values: object) -> str:
    """A fixed text in the register of the turn's person, then filled in
    (the values, such as the person's own statements, are never rewritten)."""
    table = TEXTS.get(language, TEXTS["en"])
    template = addressed(table[key], language, extra=FAMILIAR.get(language, ()))
    return template.format(**values)
