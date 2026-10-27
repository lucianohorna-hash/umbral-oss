"""Personal OSS server adapter; never reads a laptop credential file.

The caller must provision an authorized session separately. Server storage and
refresh ownership are independent of narrative canon and browser sessions.
"""
import threading
from .chatgpt_plan import ChatGPTPlanNarrator


class _StoredRuntime(ChatGPTPlanNarrator):
    def __init__(self, lease):
        self.lease = lease
        self.profile = lease.read()
        self.host = self.profile['host_id']
        self.lock = threading.RLock()
        self.pending = None

    def _save(self):
        self.lease.save(self.profile)


class SelfHostedPlanNarrator:
    name = 'chatgpt-plan-self-hosted'

    def __init__(self, store, origin):
        from urllib.parse import urlsplit
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('El narrador personal del servidor requiere un origen HTTPS válido.')
        self.store, self.origin = store, origin.rstrip('/')
        # Persist the server's own host before any future session transfer.
        with store.lease() as lease:
            lease.read()

    def _call(self, action):
        error, result = None, None
        with self.store.lease() as lease:
            runtime = _StoredRuntime(lease)
            try:
                result = action(runtime)
            except Exception as exc:
                # A completed OAuth refresh must persist even when the following
                # inference/catalog request fails. Otherwise a rotated token is lost.
                error = exc
        if error is not None:
            raise error
        return result

    def status(self):
        def inspect(runtime):
            result, profile = runtime.status(), runtime.profile
            result['session_present'] = bool(profile.get('access_token'))
            result['connected'] = result['session_present'] and profile.get('server_verified') is True
            result['needs_verification'] = result['session_present'] and not result['connected']
            return result
        result = self._call(inspect)
        result['mode'] = 'personal-oss-server'
        result['message'] = ('Narrador personal en el servidor. Usa los límites autorizados de tu plan, sin respaldo de API de pago.'
            if result['connected'] else 'Sesión recibida; falta verificar el acceso al catálogo del plan.' if result['needs_verification'] else
            'Falta trasladar una sesión autorizada desde la instalación personal. El servidor todavía no puede narrar.')
        return result

    def models(self):
        def verified(runtime):
            models = runtime.models()
            runtime.profile['server_verified'] = True
            if runtime.profile.get('model') not in {model['id'] for model in models}:
                runtime.profile['model'] = ''
            runtime._save()
            return models
        return self._call(verified)

    def select_model(self, model):
        def select(runtime):
            runtime.select_model(model)
            runtime.profile['server_verified'] = True
            runtime._save()
        return self._call(select)

    def narrate(self, context, turns):
        def generate(runtime):
            if runtime.profile.get('server_verified') is not True:
                raise ValueError('Verifica primero la conexión del plan en el servidor. Tu intervención sigue guardada.')
            return runtime.narrate(context, turns)
        return self._call(generate)

    def handoff_descriptor(self):
        from .plan_handoff import challenge
        def prepare(runtime):
            profile, descriptor = challenge(runtime.profile, self.origin)
            runtime.profile = profile
            runtime._save()
            return descriptor
        return self._call(prepare)

    def import_handoff(self, packet):
        from .plan_handoff import accept
        def receive(runtime):
            profile, replay = accept(runtime.profile, packet, self.origin)
            if not replay:
                runtime.profile = profile
                runtime._save()
            return dict(received=True, replay=replay, transfer_id=packet['transfer_id'])
        return self._call(receive)

    def disconnect(self):
        result = self._call(lambda runtime: runtime.disconnect())
        result['message'] = ('Conexión del servidor cerrada.' if result['remote_revoked'] else
            'Conexión del servidor cerrada; revocación remota no confirmada. Revisa el acceso de Umbral en los ajustes de ChatGPT.')
        return result
