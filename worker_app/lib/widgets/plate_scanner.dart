import 'package:flutter/material.dart';
import 'package:google_mlkit_text_recognition/google_mlkit_text_recognition.dart';
import 'package:image_picker/image_picker.dart';

import '../domain/plates.dart';
import 'common.dart';

/// Takes a photo of the number plate with the phone camera, runs on-device OCR
/// (ML Kit, no network) and returns the most plausible normalised Indian plate,
/// or null if the worker cancelled / nothing readable was found.
Future<String?> scanPlate(BuildContext context, {List<String>? stateCodes}) async {
  final picker = ImagePicker();
  XFile? photo;
  try {
    photo = await picker.pickImage(
      source: ImageSource.camera,
      preferredCameraDevice: CameraDevice.rear,
      maxWidth: 1600,
      imageQuality: 85,
    );
  } catch (e) {
    if (context.mounted) showSnack(context, 'Camera unavailable: $e', error: true);
    return null;
  }
  if (photo == null) return null;
  final recognizer = TextRecognizer(script: TextRecognitionScript.latin);
  try {
    final result = await recognizer.processImage(InputImage.fromFilePath(photo.path));
    final lines = <String>[
      for (final block in result.blocks)
        for (final line in block.lines) line.text,
    ];
    final plate = extractPlate(lines, stateCodes == null || stateCodes.isEmpty ? null : stateCodes);
    if (plate == null && context.mounted) {
      showSnack(context, 'Could not read a plate. Try again closer, or type it.', error: true);
    }
    return plate;
  } catch (e) {
    if (context.mounted) showSnack(context, 'OCR failed: $e', error: true);
    return null;
  } finally {
    await recognizer.close();
  }
}
