/// Browser build: no on-device OCR engine. The scan button is hidden (see [plateOcrAvailable]).
const bool plateOcrAvailable = false;

Future<List<String>> recognizeTextLines(String imagePath) async => const [];
