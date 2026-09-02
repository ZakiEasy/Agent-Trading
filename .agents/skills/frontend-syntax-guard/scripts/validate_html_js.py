#!/usr/bin/env python3
"""
Script autonome de validation syntaxique pour les scripts inline HTML et fichiers JS.
Usage:
    python validate_html_js.py <chemin_fichier_html_ou_js>
"""

import sys
import os
import re
import subprocess
import tempfile

def validate_file(filepath):
    if not os.path.exists(filepath):
        print(f"❌ Erreur: Le fichier '{filepath}' est introuvable.")
        return 1

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Si c'est un fichier JS pur
    if filepath.endswith(".js"):
        res = subprocess.run(["node", "--check", filepath], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"❌ Erreur de syntaxe dans {filepath}:\n{res.stderr}")
            return 1
        print(f"✅ {filepath}: 100% syntactiquement valide.")
        return 0

    # Extraction des balises <script> inline dans les fichiers HTML
    script_pattern = re.compile(r"(<script(?:\s+[^>]*)?>)([\s\S]*?)(</script>)", re.IGNORECASE)
    matches = list(script_pattern.finditer(content))

    if not matches:
        print(f"ℹ️ Aucun script inline trouvé dans {filepath}.")
        return 0

    total_inline = 0
    errors_found = 0

    for idx, match in enumerate(matches, start=1):
        opening_tag = match.group(1)
        code = match.group(2)
        start_offset = match.start(2)

        # Calculer le numéro de ligne dans le fichier HTML source
        line_in_source = content[:start_offset].count("\n") + 1

        # Ignorer les scripts externes (src="...")
        if "src=" in opening_tag:
            continue

        if not code.strip():
            continue

        total_inline += 1

        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as tmp:
            tmp_name = tmp.name
            tmp.write(code)

        try:
            res = subprocess.run(["node", "--check", tmp_name], capture_output=True, text=True)
            if res.returncode != 0:
                errors_found += 1
                # Extraire la ligne relative dans l'erreur Node.js
                err_lines = res.stderr.strip().split("\n")
                print(f"\n❌ ERREUR DE SYNTAXE dans {filepath} (Bloc <script> #{total_inline}) :")
                print(f"📍 Ligne approximative dans le fichier HTML : ~{line_in_source}")
                print(f"Détail Node.js :\n{res.stderr}\n")
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    if errors_found > 0:
        print(f"\n🚨 ÉCHEC : {errors_found} bloc(s) avec des erreurs de syntaxe dans {filepath}.")
        return 1

    print(f"✅ VALIDATION RÉUSSIE : {total_inline} bloc(s) <script> dans '{filepath}' sont 100% syntactiquement valides.")
    return 0

if __name__ == "__main__":
    if len(sys.argv) < 2:
        target = "templates/index.html"
    else:
        target = sys.argv[1]

    sys.exit(validate_file(target))
