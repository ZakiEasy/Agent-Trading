import re
import os
import subprocess
import unittest

class TestFrontendIntegrity(unittest.TestCase):
    def setUp(self):
        self.template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
        with open(self.template_path, "r", encoding="utf-8") as f:
            self.html = f.read()

    def test_javascript_syntax(self):
        """Vérifie que tous les blocs JavaScript inline ne contiennent aucune erreur de syntaxe."""
        regex = re.compile(r"<script(?:\s+[^>]*)?>([\s\S]*?)</script>", re.IGNORECASE)
        matches = regex.findall(self.html)
        
        inline_scripts = [m for m in matches if m.strip()]
        self.assertGreater(len(inline_scripts), 0, "Aucun script inline trouvé dans index.html")

        for idx, script in enumerate(inline_scripts):
            tmp_path = f"/tmp/test_integrity_script_{idx}.js"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(script)
            
            try:
                res = subprocess.run(["node", "--check", tmp_path], capture_output=True, text=True)
                self.assertEqual(
                    res.returncode, 0, 
                    f"Erreur de syntaxe JavaScript dans index.html (bloc {idx}) :\n{res.stderr}"
                )
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    def test_key_ui_functions_exist(self):
        """Vérifie que les fonctions JavaScript essentielles pour le menu, les onglets et les boutons sont définies."""
        essential_functions = [
            "switchTab",
            "loadRobotStatusAndData",
            "openRobotSettingsModal",
            "closeRobotSettingsModal",
            "saveRobotSettings",
            "openNewProposalModal",
            "closeNewProposalModal",
            "submitNewProposal",
            "openEditProposalModal",
            "closeEditProposalModal",
            "submitEditProposal",
            "approveRobotProposal",
            "rejectRobotProposal",
            "triggerKillSwitch",
            "resetKillSwitch",
            "toggleUsMarket",
            "renderPendingProposals",
            "renderActiveManagedPositions"
        ]

        for fn in essential_functions:
            self.assertIn(f"function {fn}", self.html, f"La fonction vitale {fn} est introuvable dans index.html")

    def test_key_dom_elements_exist(self):
        """Vérifie que les éléments DOM et modaux requis par les scripts existent dans index.html."""
        essential_ids = [
            "new-proposal-modal",
            "edit-proposal-modal",
            "robot-settings-modal",
            "stat-robot-ceiling-eur",
            "stat-robot-ceiling-usd",
            "pending-proposals-list",
            "prop-capital-input",
            "prop-quantity-input",
            "edit-prop-capital-input",
            "edit-prop-quantity-input"
        ]

        for elem_id in essential_ids:
            self.assertIn(f'id="{elem_id}"', self.html, f"L'élément id='{elem_id}' est introuvable dans index.html")

if __name__ == "__main__":
    unittest.main()
