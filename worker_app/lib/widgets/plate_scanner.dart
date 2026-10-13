import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../domain/plates.dart';
import '../platform/plate_ocr.dart';
import 'common.dart';

export '../platform/plate_ocr.dart' show plateOcrAvailable;

/// Takes a photo of the number plate with the phone camera, runs on-device OCR
/// (ML Kit, no network) and returns the most plausible normalised Indian plate,
/// or null if the worker cancelled / nothing readable was found.
Future<String?> scanPlate(BuildContext context, {List<String>? stateCodes}) async {
  if (!plateOcrAvailable) {
    showSnack(context, 'Plate scanning is available in the installed app. Type the plate instead.', error: true);
    return null;
  }
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
  try {
    final lines = await recognizeTextLines(photo.path);
    final plate = extractPlate(lines, stateCodes == null || stateCodes.isEmpty ? null : stateCodes);
    if (plate == null && context.mounted) {
      showSnack(context, 'Could not read a plate. Try again closer, or type it.', error: true);
    }
    return plate;
  } catch (e) {
    if (context.mounted) showSnack(context, 'OCR failed: $e', error: true);
    return null;
  }
}
