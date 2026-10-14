"""Preserve actual execution identity through subsequent matrix copies."""
from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'wannier_mcp/workflow/tools'
sys.path.insert(0, str(TOOLS))
import wannier_workflow as workflow


class ReceiptedCopies(unittest.TestCase):
    def test_a_later_snapshot_cannot_bless_changed_matrix(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'; source.mkdir()
            destination = Path(temp) / 'destination'; destination.mkdir()
            path = source / 'wannier90.amn'
            path.write_bytes(b'invented original matrix bytes\n')
            receipt = hashlib.sha256(path.read_bytes()).hexdigest()
            workflow.copy_receipted_matrix(source, destination, path.name, receipt)
            self.assertEqual((destination/path.name).read_bytes(), path.read_bytes())
            path.write_bytes(b'invented changed matrix bytes\n')
            with self.assertRaisesRegex(ValueError, 'execution receipt before copying'):
                workflow.copy_receipted_matrix(source, destination, path.name, receipt)

    def test_copy_time_mutation_is_rejected_against_same_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'; source.mkdir()
            destination = Path(temp) / 'destination'; destination.mkdir()
            path = source / 'wannier90.mmn'
            path.write_bytes(b'invented original matrix bytes\n')
            receipt = hashlib.sha256(path.read_bytes()).hexdigest()
            def changed_copy(original, copied):
                original.write_bytes(b'invented mutation during copy\n')
                copied.write_bytes(original.read_bytes())
            with patch.object(workflow.shutil, 'copyfile', changed_copy):
                with self.assertRaisesRegex(ValueError, 'changed while copying'):
                    workflow.copy_receipted_matrix(source, destination, path.name, receipt)
            with self.assertRaisesRegex(ValueError, 'unchanged interface matrix'):
                workflow.copy_receipted_matrix(source, destination, 'wannier90.win', receipt)


if __name__ == '__main__':
    unittest.main()
