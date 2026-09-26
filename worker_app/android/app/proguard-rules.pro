# ML Kit text recognition: the plugin references optional script packs (Chinese, Devanagari,
# Japanese, Korean) that this app does not bundle — plates are read with the Latin recogniser.
-dontwarn com.google.mlkit.vision.text.chinese.**
-dontwarn com.google.mlkit.vision.text.devanagari.**
-dontwarn com.google.mlkit.vision.text.japanese.**
-dontwarn com.google.mlkit.vision.text.korean.**
