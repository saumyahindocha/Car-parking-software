// Database factory for the current platform: native SQLite on Android/iOS,
// SQLite compiled to WebAssembly (stored in IndexedDB) in the browser.
export 'db_factory_io.dart' if (dart.library.js_interop) 'db_factory_web.dart';
