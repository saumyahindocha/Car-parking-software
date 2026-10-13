// On-device plate OCR: ML Kit on Android/iOS; not available in the browser build.
export 'plate_ocr_mlkit.dart' if (dart.library.js_interop) 'plate_ocr_web.dart';
