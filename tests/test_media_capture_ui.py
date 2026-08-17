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
        self.assertIn("if (state.problemCameraReturnToForm) $('#createDialog').close();", script)
        self.assertIn("$('#createDialog').showModal();", script)

    def test_shift_report_offers_current_and_batch_photo_files(self):
        html = (ROOT / 'kpi_static' / 'shift_test.html').read_text(encoding='utf-8')
        script = (ROOT / 'kpi_static' / 'shift_test.js').read_text(encoding='utf-8')

        self.assertIn('id="systemCamera"', html)
        self.assertIn('id="batchPhotos"', html)
        self.assertIn('id="cameraFileButton"', html)
        self.assertIn('id="batchOrderStage"', html)
        self.assertIn('id="batchQuestionList"', html)
        self.assertIn('id="batchReviewStage"', html)
        self.assertIn('id="batchReviewList"', html)
        self.assertIn('id="batchReplaceInput"', html)
        self.assertIn('id="batchPhotoInput" type="file" accept="image/*" multiple', html)
        self.assertNotIn('capture="environment"', html)
        self.assertIn("const remaining = questions.slice(runtime.draft.photo_index);", script)
        self.assertIn('function renderBatchOrder()', script)
        self.assertIn('function renderBatchReview()', script)
        self.assertIn("event.target.closest('[data-batch-move]')", script)
        self.assertIn("event.target.closest('[data-batch-replace]')", script)
        self.assertIn("$('#confirmBatchPhotos').addEventListener('click'", script)
        self.assertIn('await putPhoto(question.id, blob);', script)
        self.assertIn('files.length > remaining.length', script)
        selection_handler = script.split("$('#batchPhotoInput').addEventListener", 1)[1]
        selection_handler = selection_handler.split("$('#cancelBatchOrder').addEventListener", 1)[0]
        self.assertNotIn('putPhoto(', selection_handler)
        confirm_handler = script.split("$('#confirmBatchPhotos').addEventListener", 1)[1]
        confirm_handler = confirm_handler.split("$('#photoReview').addEventListener", 1)[0]
        self.assertIn('putPhoto(', confirm_handler)
        self.assertIn('saveDraft();', confirm_handler)

    def test_started_shift_draft_can_be_reset_without_creating_a_new_run(self):
        html = (ROOT / 'kpi_static' / 'shift_test.html').read_text(encoding='utf-8')
        script = (ROOT / 'kpi_static' / 'shift_test.js').read_text(encoding='utf-8')

        self.assertIn('id="discardDraft" type="button">Сбросить черновик', html)
        reset_handler = script.split("$('#discardDraft').addEventListener", 1)[1]
        reset_handler = reset_handler.split("window.addEventListener('omg:navigation-back'", 1)[0]
        self.assertIn('if (runtime.draft.started_at)', reset_handler)
        self.assertIn('await clearDraftPhotos(runtime.draft);', reset_handler)
        self.assertIn("runtime.draft.stage = 'checklist';", reset_handler)
        self.assertIn('runtime.draft.answers = {};', reset_handler)
        self.assertIn('runtime.draft.photo_ids = [];', reset_handler)
        self.assertIn('await startFreshScenario();', reset_handler)
        self.assertIn("$('#discardDraft').hidden = false;", script)


if __name__ == '__main__':
    unittest.main()
