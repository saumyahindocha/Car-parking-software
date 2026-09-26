import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../state/app_state.dart';
import '../widgets/common.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  late final TextEditingController _user;
  final _pin = TextEditingController();
  late final TextEditingController _server;
  bool _busy = false;
  bool _showServer = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final app = context.read<AppState>();
    _user = TextEditingController(text: app.lastUsername);
    _server = TextEditingController(text: app.serverUrl);
    _error = app.logoutReason;
  }

  Future<void> _login() async {
    final app = context.read<AppState>();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (_server.text.trim() != app.serverUrl) await app.setServerUrl(_server.text);
      await app.login(_user.text, _pin.text);
    } on ApiException catch (e) {
      setState(() => _error = e.statusCode == 401 ? 'Wrong username or PIN' : e.detail);
    } on NetworkException {
      setState(() {
        _error = 'Cannot reach the parking server at ${app.serverUrl}. Connect to the lot Wi-Fi.';
        _showServer = true;
      });
    } catch (e) {
      setState(() => _error = errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Icon(Icons.local_parking, size: 72, color: upiBlue),
                const SizedBox(height: 8),
                const Text('Parking Worker', textAlign: TextAlign.center, style: TextStyle(fontSize: 28, fontWeight: FontWeight.w800)),
                const SizedBox(height: 32),
                TextField(
                  controller: _user,
                  textInputAction: TextInputAction.next,
                  autocorrect: false,
                  decoration: const InputDecoration(labelText: 'Username', prefixIcon: Icon(Icons.person), border: OutlineInputBorder()),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _pin,
                  obscureText: true,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  onSubmitted: (_) => _login(),
                  decoration: const InputDecoration(labelText: 'PIN', prefixIcon: Icon(Icons.pin), border: OutlineInputBorder()),
                ),
                const SizedBox(height: 16),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Text(_error!, style: const TextStyle(color: dueRed, fontSize: 15)),
                  ),
                BigButton(label: _busy ? 'Logging in…' : 'Log in', icon: Icons.login, onPressed: _busy ? null : _login),
                const SizedBox(height: 24),
                TextButton.icon(
                  onPressed: () => setState(() => _showServer = !_showServer),
                  icon: const Icon(Icons.dns, size: 18),
                  label: Text('Server: ${app.serverUrl}'),
                ),
                if (_showServer) ...[
                  TextField(
                    controller: _server,
                    keyboardType: TextInputType.url,
                    autocorrect: false,
                    decoration: InputDecoration(
                      labelText: 'Server URL',
                      helperText: 'Default $defaultServerUrl',
                      border: const OutlineInputBorder(),
                      suffixIcon: IconButton(
                        icon: const Icon(Icons.restart_alt),
                        tooltip: 'Reset to default',
                        onPressed: () => _server.text = defaultServerUrl,
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton(
                    onPressed: () async {
                      await app.setServerUrl(_server.text);
                      final ok = await app.api.health();
                      if (context.mounted) {
                        showSnack(context, ok ? 'Server reachable' : 'Server not reachable', error: !ok);
                      }
                    },
                    child: const Text('Save & test connection'),
                  ),
                ],
                const SizedBox(height: 16),
                Text(
                  'This phone: ${app.deviceId.substring(0, 8)}…\nWorker and guard accounts are bound to one phone at first login. '
                  'Ask the admin to reset if you change phones.',
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.black45, fontSize: 12),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
