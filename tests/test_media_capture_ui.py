import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MediaCaptureUiTests(unittest.TestCase):
    def test_taskboard_uses_one_file_picker_and_embedded_camera(self):
        html = (ROOT / 'kpi_static' / 'problems.html').read_text(encoding='utf-8')
        script = (ROOT / 'kpi_static' / 'problems.js').read_text(encoding='utf-8')

        self.assertIn('id="attachProblemFile"', html)
        self.assertIn('id="openProblemCamera"', html)
        self.assertIn('id="problemCameraStage"', html)
        self.assertNotIn('name="photo" type="file"', html)
        self.assertNotIn('name="video" type="file"', html)
        self.assertIn("data.set('photo', state.problemMedia.blob", script)
        self.assertIn("data.set('video', state.problemMedia.blob", script)
        self.assertIn('new MediaRecorder(', script)

    def test_shift_report_offers_current_and_batch_photo_files(self):
        html = (ROOT / 'kpi_static' / 'shift_test.html').read_text(encoding='utf-8')
        script = (ROOT / 'kpi_static' / 'shift_test.js').read_text(encoding='utf-8')

        self.assertIn('id="systemCamera"', html)
        self.assertIn('id="batchPhotos"', html)
        self.assertIn('id="cameraFileButton"', html)
        self.assertIn('id="batchPhotoInput" type="file" accept="image/*" multiple', html)
        self.assertNotIn('capture="environment"', html)
        self.assertIn("const remaining = questions.slice(runtime.draft.photo_index);", script)
        self.assertIn('await putPhoto(question.id, blob);', script)
        self.assertIn('files.length > remaining.length', script)


if __name__ == '__main__':
    unittest.main()
