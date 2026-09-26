import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'screens/home_screen.dart';
import 'screens/login_screen.dart';
import 'state/app_state.dart';
import 'widgets/common.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]);
  final app = await AppState.create();
  runApp(ParkingWorkerApp(app: app));
}

class ParkingWorkerApp extends StatelessWidget {
  const ParkingWorkerApp({super.key, required this.app});
  final AppState app;

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider<AppState>.value(
      value: app,
      child: MaterialApp(
        title: 'Parking Worker',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: upiBlue),
          useMaterial3: true,
          visualDensity: VisualDensity.standard,
        ),
        home: Consumer<AppState>(
          builder: (_, s, _) => s.loggedIn ? const HomeScreen() : const LoginScreen(),
        ),
      ),
    );
  }
}
