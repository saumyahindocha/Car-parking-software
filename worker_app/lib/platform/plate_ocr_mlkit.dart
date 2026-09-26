import 'package:google_mlkit_text_recognition/google_mlkit_text_recognition.dart';

const bool plateOcrAvailable = true;

/// Text lines found in the photo, using ML Kit's Latin recogniser (no network).
Future<List<String>> recognizeTextLines(String imagePath) async {
  final recognizer = TextRecognizer(script: TextRecognitionScript.latin);
  try {
    final result = await recognizer.processImage(InputImage.fromFilePath(imagePath));
    return [
      for (final block in result.blocks)
        for (final line in block.lines) line.text,
    ];
  } finally {
    await recognizer.close();
  }
}
