from flask import Flask, render_template, request, redirect, session, flash, jsonify
from datetime import datetime, timedelta
import json
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = 'clave_secreta_muy_segura_para_produccion_cambiar'

# ==================== CONFIGURACIÓN ====================
ROLES_PERMISOS = {
    'contratista': {
        'permisos': ['radicar_cuenta', 'ver_propias', 'corregir_cuenta', 'ver_estado'],
        'estados_permitidos': ['radicado', 'devuelto'],
        'descripcion': 'Puede radicar y corregir cuentas de cobro'
    },
    'epb': {
        'permisos': ['aprobar', 'devolver', 'iniciar_revision', 'ver_todas', 'dashboard'],
        'estados_permitidos': ['revision_epb'],
        'descripcion': 'Revisa y aprueba cuentas en primera instancia'
    },
    'supervisor': {
        'permisos': ['aprobar', 'devolver', 'iniciar_revision', 'ver_todas', 'dashboard'],
        'estados_permitidos': ['revision_supervisor'],
        'descripcion': 'Supervisa y aprueba cuentas'
    },
    'general': {
        'permisos': ['aprobar', 'devolver', 'iniciar_revision', 'ver_todas', 'dashboard'],
        'estados_permitidos': ['revision_general'],
        'descripcion': 'Revisión final antes de hacienda'
    },
    'hacienda': {
        'permisos': ['aprobar', 'devolver', 'marcar_pagado', 'ver_todas', 'dashboard'],
        'estados_permitidos': ['revision_hacienda', 'pagado'],
        'descripcion': 'Realiza el pago final'
    }
}

ESTADOS_FLUJO = [
    'radicado',           # Estado inicial
    'revision_epb',       # En revisión por EPB
    'revision_supervisor', # En revisión por Supervisor
    'revision_general',   # En revisión por Secretaría General
    'revision_hacienda',  # En revisión por Hacienda
    'pagado',             # Estado final - Pagado
    'devuelto'            # Devuelto para correcciones
]

# ==================== DECORADORES DE SEGURIDAD ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Debe iniciar sesión para acceder a esta página', 'error')
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

def rol_required(rol):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_rol' not in session or session['user_rol'] != rol:
                flash(f'No tiene permisos de {rol} para acceder a esta página', 'error')
                return redirect('/dashboard')
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def permiso_required(permiso):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_rol = session.get('user_rol')
            if not user_rol or permiso not in ROLES_PERMISOS.get(user_rol, {}).get('permisos', []):
                flash(f'No tiene permiso para: {permiso}', 'error')
                return redirect('/dashboard')
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==================== FUNCIONES DE BASE DE DATOS ====================
def cargar_usuarios():
    if os.path.exists('usuarios.json'):
        with open('usuarios.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def guardar_usuarios(usuarios):
    with open('usuarios.json', 'w', encoding='utf-8') as f:
        json.dump(usuarios, f, indent=2, ensure_ascii=False)

def cargar_cuentas():
    if os.path.exists('cuentas.json'):
        with open('cuentas.json', 'r', encoding='utf-8') as f:
            cuentas = json.load(f)
            # Asegurar que todas las cuentas tengan campos nuevos
            for cuenta in cuentas:
                cuenta.setdefault('alertas', [])
                cuenta.setdefault('dias_por_etapa', {})
            return cuentas
    return []

def guardar_cuentas(cuentas):
    with open('cuentas.json', 'w', encoding='utf-8') as f:
        json.dump(cuentas, f, indent=2, ensure_ascii=False)

# ==================== FUNCIONES DE CALCULO DE TIEMPOS ====================
def calcular_tiempo_entre_fechas(fecha_inicio, fecha_fin):
    """Calcula días hábiles entre dos fechas"""
    if not fecha_inicio or not fecha_fin:
        return 0
    
    inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d %H:%M:%S')
    fin = datetime.strptime(fecha_fin, '%Y-%m-%d %H:%M:%S')
    
    # Diferencia en días naturales
    dias_naturales = (fin - inicio).days
    
    return dias_naturales

def verificar_alerta_3_dias(cuenta):
    """Verifica si alguna etapa lleva más de 3 días"""
    alertas = []
    timestamps = cuenta.get('timestamps', {})
    estado_actual = cuenta['estado_actual']
    
    # Verificar según el estado actual
    if estado_actual == 'revision_epb' and 'inicio_revision_epb' in timestamps:
        dias = calcular_tiempo_entre_fechas(timestamps['inicio_revision_epb'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if dias > 3:
            alertas.append(f'Revisión EPB lleva {dias} días (máximo 3)')
    
    elif estado_actual == 'revision_supervisor' and 'inicio_revision_supervisor' in timestamps:
        dias = calcular_tiempo_entre_fechas(timestamps['inicio_revision_supervisor'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if dias > 3:
            alertas.append(f'Revisión Supervisor lleva {dias} días (máximo 3)')
    
    elif estado_actual == 'revision_general' and 'inicio_revision_general' in timestamps:
        dias = calcular_tiempo_entre_fechas(timestamps['inicio_revision_general'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if dias > 3:
            alertas.append(f'Revisión General lleva {dias} días (máximo 3)')
    
    elif estado_actual == 'revision_hacienda' and 'inicio_revision_hacienda' in timestamps:
        dias = calcular_tiempo_entre_fechas(timestamps['inicio_revision_hacienda'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if dias > 3:
            alertas.append(f'Revisión Hacienda lleva {dias} días (máximo 3)')
    
    return alertas

# ==================== SISTEMA DE ASIGNACIÓN AUTOMÁTICA ====================
def obtener_usuario_por_rol_y_dependencia(rol, dependencia=None):
    """Obtiene un usuario activo por rol y dependencia"""
    usuarios = cargar_usuarios()
    usuarios_filtrados = [u for u in usuarios if u.get('rol') == rol and u.get('activo', True)]
    
    if dependencia:
        usuarios_filtrados = [u for u in usuarios_filtrados if u.get('dependencia') == dependencia]
    
    # Por simplicidad, tomamos el primer usuario disponible
    return usuarios_filtrados[0] if usuarios_filtrados else None

def asignar_siguiente_responsable(cuenta, estado_anterior, nuevo_estado):
    """Asigna automáticamente el siguiente responsable según el estado"""
    usuarios = cargar_usuarios()
    
    # Mapeo de estados a roles responsables
    mapeo_estado_rol = {
        'radicado': 'epb',
        'revision_epb': 'supervisor',
        'revision_supervisor': 'general',
        'revision_general': 'hacienda',
        'revision_hacienda': 'hacienda',
        'devuelto': 'contratista'
    }
    
    rol_responsable = mapeo_estado_rol.get(nuevo_estado)
    
    if not rol_responsable:
        return None
    
    # Para el estado devuelto, asignar al contratista original
    if nuevo_estado == 'devuelto':
        return next((u for u in usuarios if u['id'] == cuenta.get('contratista_id')), None)
    
    # Para otros estados, buscar usuario del rol correspondiente
    if rol_responsable == 'supervisor':
        return obtener_usuario_por_rol_y_dependencia('supervisor')
    elif rol_responsable == 'general':
        return obtener_usuario_por_rol_y_dependencia('general')
    elif rol_responsable == 'hacienda':
        return obtener_usuario_por_rol_y_dependencia('hacienda')
    elif rol_responsable == 'epb':
        return obtener_usuario_por_rol_y_dependencia('epb')
    
    return None

# ==================== RUTAS DE AUTENTICACIÓN ====================
@app.route('/')
def index():
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']  # hash seguro
        
        usuarios = cargar_usuarios()
        usuario = next((u for u in usuarios if u['username'] == username and u['password'] == password), None)
        
        if usuario:
            session['user_id'] = usuario['id']
            session['user_rol'] = usuario['rol']
            session['user_nombre'] = usuario['nombre']
            flash(f'Bienvenido {usuario["nombre"]}', 'success')
            return redirect('/dashboard')
        else:
            flash('Usuario o contraseña incorrectos', 'error')
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Login - Sistema Cuentas de Cobro</title>
        <style>
            body { font-family: Arial; margin: 40px; background: #f5f5f5; }
            .login-box { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width: 400px; margin: 100px auto; }
            input[type="text"], input[type="password"] { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }
            button { background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; width: 100%; }
            .error { color: red; margin: 10px 0; }
            .success { color: green; margin: 10px 0; }
        </style>
    </head>
    <body>
        <div class="login-box">
            <h2>🚀 Sistema de Cuentas de Cobro</h2>
            <form method="POST">
                <input type="text" name="username" placeholder="Usuario" required>
                <input type="password" name="password" placeholder="Contraseña" required>
                <button type="submit">Iniciar Sesión</button>
            </form>
            <p><small>¿Primera vez? <a href="/crear-usuario">Crear usuario</a></small></p>
        </div>
    </body>
    </html>
    '''

@app.route('/logout')
def logout():
    session.clear()
    flash('Sesión cerrada correctamente', 'success')
    return redirect('/login')

# ==================== ADMINISTRACIÓN DE USUARIOS ====================
@app.route('/crear-usuario', methods=['GET', 'POST'])
def crear_usuario():
    if request.method == 'POST':
        usuarios = cargar_usuarios()
        
        nuevo_usuario = {
            'id': len(usuarios) + 1,
            'username': request.form['username'],
            'password': request.form['password'],
            'rol': request.form['rol'],
            'nombre': request.form['nombre'],
            'email': request.form.get('email', ''),
            'dependencia': request.form.get('dependencia', ''),
            'activo': True,
            'fecha_creacion': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        usuarios.append(nuevo_usuario)
        guardar_usuarios(usuarios)
        
        flash(f'Usuario {nuevo_usuario["nombre"]} creado exitosamente', 'success')
        return redirect('/login')
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Crear Usuario</title>
        <style>
            body { font-family: Arial; margin: 40px; background: #f5f5f5; }
            .form-box { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width: 500px; margin: 50px auto; }
            input, select { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }
            button { background: #28a745; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; width: 100%; }
        </style>
    </head>
    <body>
        <div class="form-box">
            <h2>👤 Crear Nuevo Usuario</h2>
            <form method="POST">
                <input type="text" name="nombre" placeholder="Nombre completo" required>
                <input type="text" name="username" placeholder="Nombre de usuario" required>
                <input type="password" name="password" placeholder="Contraseña" required>
                <select name="rol" required onchange="mostrarDependencia(this)">
                    <option value="">Seleccionar rol</option>
                    <option value="contratista">Contratista</option>
                    <option value="epb">Administrador EPB</option>
                    <option value="supervisor">Supervisor</option>
                    <option value="general">Secretaría General</option>
                    <option value="hacienda">Hacienda</option>
                </select>
                <div id="dependencia-field" style="display: none;">
                    <input type="text" name="dependencia" placeholder="Dependencia/Área (Ej: Calidad, Finanzas, etc.)">
                </div>
                <input type="email" name="email" placeholder="Email (opcional)">
                <button type="submit">Crear Usuario</button>
            </form>
            <script>
                function mostrarDependencia(select) {
                    const dependenciaField = document.getElementById('dependencia-field');
                    const rolesConDependencia = ['supervisor', 'general', 'hacienda'];
                    if (rolesConDependencia.includes(select.value)) {
                        dependenciaField.style.display = 'block';
                    } else {
                        dependenciaField.style.display = 'none';
                    }
                }
            </script>
            <p><a href="/login">← Volver al login</a></p>
        </div>
    </body>
    </html>
    '''

# ==================== RUTAS DE RADICACIÓN ====================
@app.route('/radicar', methods=['GET', 'POST'])
@login_required
@permiso_required('radicar_cuenta')
def radicar_cuenta():
    if request.method == 'POST':
        cuentas = cargar_cuentas()
        
        # Obtener el primer usuario EPB para asignación automática
        usuario_epb = obtener_usuario_por_rol_y_dependencia('epb')
        
        if not usuario_epb:
            flash('❌ No hay usuarios EPB disponibles para asignar la revisión', 'error')
            return redirect('/radicar')
        
        # Generar número de cuenta automático
        numero_cuenta = f"CC-{datetime.now().strftime('%Y%m%d')}-{len(cuentas) + 1:03d}"
        
        nueva_cuenta = {
            'id': len(cuentas) + 1,
            'numero_cuenta': numero_cuenta,
            'contratista_id': session['user_id'],
            'contratista_nombre': session['user_nombre'],
            'numero_contrato': request.form['numero_contrato'],
            'numero_acta': request.form['numero_acta'],
            'valor': float(request.form['valor']),
            'descripcion': request.form['descripcion'],
            'estado_actual': 'revision_epb',  # ✅ CAMBIADO de 'radicado' a 'revision_epb'
            'responsable_actual': usuario_epb['id'],
            'responsable_nombre': usuario_epb['nombre'],
            'timestamps': {
                'radicacion': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'asignacion_epb': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'inicio_revision_epb': datetime.now().strftime('%Y-%m-%d %H:%M:%S')  # ✅ AGREGADO
            },
            'historial': [
                {
                    'estado': 'radicado',
                    'usuario': session['user_nombre'],
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'accion': 'radicacion',
                    'comentario': 'Cuenta radicada inicialmente'
                },
                {
                    'estado': 'revision_epb',
                    'usuario': 'Sistema',
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'accion': 'asignacion',
                    'comentario': f'Cuenta asignada automáticamente a {usuario_epb["nombre"]}',
                    'responsable_asignado': usuario_epb['nombre'],
                    'responsable_id': usuario_epb['id']
                }
            ]
        }
        cuentas.append(nueva_cuenta)
        guardar_cuentas(cuentas)
        
        flash(f'✅ Cuenta de cobro {numero_cuenta} radicada exitosamente. Asignada a: {usuario_epb["nombre"]}', 'success')
        return redirect('/cuentas')
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Radicar Cuenta de Cobro</title>
        <style>
            body { font-family: Arial; margin: 0; padding: 20px; background: #f5f5f5; }
            .form-container { background: white; padding: 30px; border-radius: 10px; max-width: 600px; margin: 20px auto; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            input, textarea, select { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }
            button { background: #28a745; color: white; padding: 12px 30px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }
            .btn-volver { background: #6c757d; margin-right: 10px; }
        </style>
    </head>
    <body>
        <div class="form-container">
            <h2>📝 Radicar Nueva Cuenta de Cobro</h2>
            <form method="POST">
                <div>
                    <label><strong>Número de Contrato *</strong></label>
                    <input type="text" name="numero_contrato" placeholder="Ej: CT-2024-001" required>
                </div>
                
                <div>
                    <label><strong>Número de Acta *</strong></label>
                    <input type="text" name="numero_acta" placeholder="Ej: AC-2024-001" required>
                </div>
                
                <div>
                    <label><strong>Valor *</strong></label>
                    <input type="number" name="valor" placeholder="Ej: 15000000" step="0.01" required>
                </div>
                
                <div>
                    <label><strong>Descripción del Servicio *</strong></label>
                    <textarea name="descripcion" rows="4" placeholder="Describa los servicios prestados..." required></textarea>
                </div>
                
                <div>
                    <button type="submit" class="btn-success">📤 Radicar Cuenta</button>
                    <a href="/cuentas" class="btn-volver" style="background: #6c757d; color: white; padding: 12px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">← Volver</a>
                </div>
            </form>
        </div>
    </body>
    </html>
    '''

# ==================== LISTA DE CUENTAS ====================
@app.route('/cuentas')
@login_required
def listar_cuentas():
    cuentas = cargar_cuentas()
    user_rol = session['user_rol']
    user_id = session['user_id']
    
    # Filtrar según el rol
    if user_rol == 'contratista':
        cuentas = [c for c in cuentas if c.get('contratista_id') == user_id]
        titulo = "Mis Cuentas de Cobro"
    else:
        titulo = "Todas las Cuentas de Cobro"
    
    # Calcular alertas para cada cuenta
    for cuenta in cuentas:
        cuenta['alertas'] = verificar_alerta_3_dias(cuenta)
    
    cuentas_html = ""
    
    for cuenta in cuentas:
        estado_color = {
            'radicado': '#ffc107',
            'revision_epb': '#17a2b8', 
            'revision_supervisor': '#fd7e14',
            'revision_general': '#20c997',
            'revision_hacienda': '#6f42c1',
            'pagado': '#28a745',
            'devuelto': '#dc3545'
        }.get(cuenta['estado_actual'], '#6c757d')
        
        alertas_html = ""
        if cuenta.get('alertas'):
            alertas_html = f"<div style='color: red; font-weight: bold; margin: 5px 0;'>⚠️ {' | '.join(cuenta['alertas'])}</div>"
        
        acciones_html = ""

        # LÓGICA SIMPLIFICADA Y CORRECTA DE ACCIONES
        if user_rol == 'admin' and cuenta['estado_actual'] in ['revision_admin', 'devuelto']:
            acciones_html = f"""
            <div style="margin-top: 10px;">
                <a href="/accion-cuenta/{cuenta['id']}/aprobar" style="background: #28a745; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-right: 5px; font-size: 12px;">✅ Aprobar</a>
                <a href="/accion-cuenta/{cuenta['id']}/devolver" style="background: #dc3545; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; font-size: 12px;">↩️ Devolver</a>
                <a href="/cuenta/{cuenta['id']}" style="background: #17a2b8; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-left: 5px; font-size: 12px;">📝 Ver Detalle</a>
            </div>
            """
        
        elif user_rol == 'general' and cuenta['estado_actual'] == 'revision_general':
            acciones_html = f"""
            <div style="margin-top: 10px;">
                <a href="/accion-cuenta/{cuenta['id']}/aprobar" style="background: #28a745; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-right: 5px; font-size: 12px;">✅ Aprobar</a>
                <a href="/accion-cuenta/{cuenta['id']}/devolver" style="background: #dc3545; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; font-size: 12px;">↩️ Devolver</a>
                <a href="/cuenta/{cuenta['id']}" style="background: #17a2b8; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-left: 5px; font-size: 12px;">📝 Ver Detalle</a>
            </div>
            """
        
        elif user_rol == 'hacienda' and cuenta['estado_actual'] == 'revision_hacienda':
            acciones_html = f"""
            <div style="margin-top: 10px;">
                <a href="/accion-cuenta/{cuenta['id']}/pagar" style="background: #28a745; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-right: 5px; font-size: 12px;">💰 Pagar</a>
                <a href="/accion-cuenta/{cuenta['id']}/devolver" style="background: #dc3545; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; font-size: 12px;">↩️ Devolver</a>
                <a href="/cuenta/{cuenta['id']}" style="background: #17a2b8; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; margin-left: 5px; font-size: 12px;">📝 Ver Detalle</a>
            </div>
            """
        
        elif user_rol == 'contratista' and cuenta['estado_actual'] == 'devuelto':
            acciones_html = f"""
            <div style="margin-top: 10px;">
                <a href="/cuenta/{cuenta['id']}" style="background: #17a2b8; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; font-size: 12px;">📝 Ver Correcciones</a>
            </div>
            """
        
        else:
            # Para otros casos, mostrar solo el botón de detalle
            acciones_html = f"""
            <div style="margin-top: 10px;">
                <a href="/cuenta/{cuenta['id']}" style="background: #17a2b8; color: white; padding: 5px 10px; text-decoration: none; border-radius: 3px; font-size: 12px;">📝 Ver Detalle</a>
            </div>
            """
        
        cuentas_html += f"""
        <div style="background: white; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid {estado_color}; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
            <div style="display: flex; justify-content: between; align-items: center;">
                <div style="flex: 1;">
                    <h3 style="margin: 0 0 5px 0;">{cuenta['numero_cuenta']}</h3>
                    <p style="margin: 2px 0; color: #666;">Contrato: {cuenta['numero_contrato']} | Acta: {cuenta['numero_acta']}</p>
                    <p style="margin: 2px 0;"><strong>Contratista:</strong> {cuenta['contratista_nombre']}</p>
                    <p style="margin: 2px 0;"><strong>Valor:</strong> ${cuenta['valor']:,.0f}</p>
                    <p style="margin: 2px 0;"><strong>Estado:</strong> <span style="background: {estado_color}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px;">{cuenta['estado_actual'].replace('_', ' ').title()}</span></p>
                    <p style="margin: 2px 0;"><strong>Responsable actual:</strong> 
                        <span style="background: #6f42c1; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px;">
                            {cuenta.get('responsable_nombre', 'No asignado')}
                        </span>
                    </p>
                    <p style="margin: 2px 0; font-size: 12px; color: #888;">Radicado: {cuenta['timestamps']['radicacion']}</p>
                </div>
            </div>
            {alertas_html}
            {acciones_html}
        </div>
        """
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>{titulo}</title>
        <style>
            body {{ font-family: Arial; margin: 0; padding: 20px; background: #f5f5f5; }}
            .header {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .btn {{ background: #007bff; color: white; padding: 10px 15px; text-decoration: none; border-radius: 5px; margin-right: 10px; display: inline-block; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📋 {titulo}</h1>
            <div>
                <a href="/dashboard" class="btn">← Dashboard</a>
                {'<a href="/radicar" class="btn" style="background: #28a745;">📝 Nueva Cuenta</a>' if session['user_rol'] == 'contratista' else ''}
            </div>
        </div>
        
        {cuentas_html if cuentas_html else '<div style="background: white; padding: 30px; text-align: center; border-radius: 8px;"><p>No hay cuentas de cobro registradas</p></div>'}
    </body>
    </html>
    '''
# ==================== ACCIONES SOBRE CUENTAS ====================
@app.route('/accion-cuenta/<int:cuenta_id>/<accion>', methods=['GET', 'POST'])
@login_required
def accion_cuenta(cuenta_id, accion):
    cuentas = cargar_cuentas()
    cuenta = next((c for c in cuentas if c['id'] == cuenta_id), None)
    
    if not cuenta:
        flash('Cuenta no encontrada', 'error')
        return redirect('/cuentas')
    
    user_rol = session['user_rol']
    estado_actual = cuenta['estado_actual']
    
    # Validar que el usuario puede realizar la acción en este estado
    if estado_actual not in ROLES_PERMISOS[user_rol]['estados_permitidos']:
        flash('No puede realizar esta acción en el estado actual de la cuenta', 'error')
        return redirect('/cuentas')
    
    # Definir el flujo de estados
    flujo_estados = {
        'radicado': 'revision_epb',
        'revision_epb': 'revision_supervisor',
        'revision_supervisor': 'revision_general', 
        'revision_general': 'revision_hacienda',
        'revision_hacienda': 'pagado'
    }
    
    timestamp_actual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if accion == 'aprobar':
        # Mover al siguiente estado
        nuevo_estado = flujo_estados[estado_actual]
        
        # Asignar siguiente responsable automáticamente
        siguiente_responsable = asignar_siguiente_responsable(cuenta, estado_actual, nuevo_estado)
        
        if not siguiente_responsable:
            flash('❌ No hay usuario disponible para asignar la siguiente etapa', 'error')
            return redirect('/cuentas')
        
        # Actualizar cuenta con nuevo estado y responsable
        cuenta['estado_actual'] = nuevo_estado
        cuenta['responsable_actual'] = siguiente_responsable['id']
        cuenta['responsable_nombre'] = siguiente_responsable['nombre']
        
        # Registrar timestamp
        timestamp_key = f"inicio_revision_{nuevo_estado.split('_')[1]}"
        cuenta['timestamps'][timestamp_key] = timestamp_actual
        cuenta['timestamps'][f'asignado_{nuevo_estado}'] = timestamp_actual
        
        # Agregar al historial
        cuenta['historial'].append({
            'estado': nuevo_estado,
            'usuario': session['user_nombre'],
            'timestamp': timestamp_actual,
            'accion': 'aprobacion',
            'comentario': f'Aprobado por {user_rol} - Avanza a {nuevo_estado.replace("_", " ").title()}',
            'responsable_asignado': siguiente_responsable['nombre'],
            'responsable_id': siguiente_responsable['id']
        })
        
        flash(f'✅ Cuenta aprobada. Asignada a: {siguiente_responsable["nombre"]}', 'success')
        guardar_cuentas(cuentas)
        return redirect('/cuentas')
    
    elif accion == 'devolver':
        # Para devoluciones, mostrar formulario de comentarios
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Devolver Cuenta</title>
            <style>
                body {{ font-family: Arial; margin: 0; padding: 20px; background: #f5f5f5; }}
                .form-container {{ background: white; padding: 30px; border-radius: 10px; max-width: 600px; margin: 50px auto; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                textarea {{ width: 100%; padding: 15px; margin: 15px 0; border: 1px solid #ddd; border-radius: 5px; font-family: Arial; font-size: 14px; }}
                .btn {{ padding: 12px 25px; border: none; border-radius: 5px; cursor: pointer; text-decoration: none; display: inline-block; margin-right: 10px; }}
                .btn-devolver {{ background: #dc3545; color: white; }}
                .btn-cancelar {{ background: #6c757d; color: white; }}
                .cuenta-info {{ background: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
            </style>
        </head>
        <body>
            <div class="form-container">
                <h2>↩️ Devolver Cuenta de Cobro</h2>
                
                <div class="cuenta-info">
                    <h3>{cuenta['numero_cuenta']}</h3>
                    <p><strong>Contrato:</strong> {cuenta['numero_contrato']}</p>
                    <p><strong>Contratista:</strong> {cuenta['contratista_nombre']}</p>
                    <p><strong>Valor:</strong> ${cuenta['valor']:,.0f}</p>
                    <p><strong>Estado actual:</strong> {estado_actual.replace('_', ' ').title()}</p>
                </div>
                
                <form method="POST" action="/procesar-devolucion/{cuenta_id}">
                    <div>
                        <label


                                            <div>
                        <label><strong>📝 Motivo de la devolución *</strong></label>
                        <textarea name="comentario" rows="6" placeholder="Describa detalladamente los motivos de la devolución, las correcciones requeridas y cualquier observación importante..." required></textarea>
                    </div>
                    
                    <div>
                        <label><strong>🔧 Tipo de corrección requerida</strong></label>
                        <select name="tipo_correccion" style="width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px;">
                            <option value="">Seleccionar tipo de corrección...</option>
                            <option value="documentacion">Documentación incompleta</option>
                            <option value="calculos">Error en cálculos o valores</option>
                            <option value="informacion">Información incorrecta</option>
                            <option value="procedimiento">Incumplimiento de procedimiento</option>
                            <option value="otros">Otros</option>
                        </select>
                    </div>
                    
                    <div style="margin-top: 20px;">
                        <button type="submit" class="btn btn-devolver">↩️ Confirmar Devolución</button>
                        <a href="/cuentas" class="btn btn-cancelar">❌ Cancelar</a>
                    </div>
                </form>
            </div>
        </body>
        </html>
        '''
    
    elif accion == 'pagar' and user_rol == 'hacienda':
        cuenta['estado_actual'] = 'pagado'
        cuenta['timestamps']['pago'] = timestamp_actual
        cuenta['historial'].append({
            'estado': 'pagado',
            'usuario': session['user_nombre'],
            'timestamp': timestamp_actual,
            'accion': 'pago',
            'comentario': 'Cuenta pagada exitosamente'
        })
        flash('💰 Cuenta marcada como pagada', 'success')
        guardar_cuentas(cuentas)
        return redirect('/cuentas')
    
    return redirect('/cuentas')

@app.route('/procesar-devolucion/<int:cuenta_id>', methods=['POST'])
@login_required
def procesar_devolucion(cuenta_id):
    cuentas = cargar_cuentas()
    cuenta = next((c for c in cuentas if c['id'] == cuenta_id), None)
    
    if not cuenta:
        flash('Cuenta no encontrada', 'error')
        return redirect('/cuentas')
    
    comentario = request.form['comentario']
    tipo_correccion = request.form.get('tipo_correccion', 'no especificado')
    timestamp_actual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Asignar al contratista para correcciones
    usuario_contratista = next((u for u in cargar_usuarios() if u['id'] == cuenta.get('contratista_id')), None)
    
    if usuario_contratista:
        cuenta['responsable_actual'] = usuario_contratista['id']
        cuenta['responsable_nombre'] = usuario_contratista['nombre']
    
    # Cambiar estado a devuelto
    cuenta['estado_actual'] = 'devuelto'
    
    # Agregar comentario detallado al historial
    cuenta['historial'].append({
        'estado': 'devuelto',
        'usuario': session['user_nombre'],
        'timestamp': timestamp_actual,
        'accion': 'devolucion',
        'comentario': comentario,
        'tipo_correccion': tipo_correccion,
        'rol_responsable': session['user_rol'],
        'responsable_asignado': usuario_contratista['nombre'] if usuario_contratista else 'No asignado',
        'responsable_id': usuario_contratista['id'] if usuario_contratista else None
    })
    
    # Guardar cambios
    guardar_cuentas(cuentas)
    
    flash('✅ Cuenta devuelta exitosamente. Asignada al contratista para correcciones.', 'success')
    return redirect('/cuentas')

# ==================== VISTA DETALLADA DE CUENTA CON COMENTARIOS ====================

@app.route('/cuenta/<int:cuenta_id>')
@login_required
def ver_cuenta_detalle(cuenta_id):
    cuentas = cargar_cuentas()
    cuenta = next((c for c in cuentas if c['id'] == cuenta_id), None)
    
    if not cuenta:
        flash('Cuenta no encontrada', 'error')
        return redirect('/cuentas')
    
    # Verificar permisos: contratistas solo ven sus cuentas
    user_rol = session['user_rol']
    user_id = session['user_id']
    if user_rol == 'contratista' and cuenta.get('contratista_id') != user_id:
        flash('No tiene permisos para ver esta cuenta', 'error')
        return redirect('/cuentas')
    
    # Generar HTML del historial con comentarios
    historial_html = ""
    for movimiento in reversed(cuenta.get('historial', [])):
        # Determinar icono y color según la acción
        if movimiento['accion'] == 'radicacion':
            icono = '📤'
            color = '#17a2b8'
        elif movimiento['accion'] == 'aprobacion':
            icono = '✅'
            color = '#28a745'
        elif movimiento['accion'] == 'devolucion':
            icono = '↩️'
            color = '#dc3545'
        elif movimiento['accion'] == 'pago':
            icono = '💰'
            color = '#20c997'
        else:
            icono = '📝'
            color = '#6c757d'
        
        # Mostrar comentario si existe
        comentario_html = ""
        if movimiento.get('comentario'):
            comentario_html = f"""
            <div style="background: #f8f9fa; padding: 10px; border-radius: 5px; margin-top: 5px; border-left: 3px solid {color};">
                <strong>Comentario:</strong> {movimiento['comentario']}
                {f"<br><strong>Tipo corrección:</strong> {movimiento.get('tipo_correccion', '').title()}" if movimiento.get('tipo_correccion') else ""}
            </div>
            """
        
        historial_html += f"""
        <div style="border-left: 3px solid {color}; padding: 15px; margin: 10px 0; background: white; border-radius: 0 8px 8px 0;">
            <div style="display: flex; align-items: center; margin-bottom: 5px;">
                <span style="font-size: 18px; margin-right: 10px;">{icono}</span>
                <div>
                    <strong>{movimiento['estado'].replace('_', ' ').title()}</strong>
                    <div style="font-size: 12px; color: #666;">
                        Por: {movimiento['usuario']} | {movimiento['timestamp']}
                    </div>
                </div>
            </div>
            {comentario_html}
        </div>
        """
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Detalle Cuenta - {cuenta["numero_cuenta"]}</title>
        <style>
            body {{ font-family: Arial; margin: 0; padding: 20px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; }}
            .header {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
            .info-section {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
            .btn {{ background: #007bff; color: white; padding: 10px 15px; text-decoration: none; border-radius: 5px; display: inline-block; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📋 Detalle de Cuenta: {cuenta["numero_cuenta"]}</h1>
                <a href="/cuentas" class="btn">← Volver a Cuentas</a>
            </div>
            
            <div class="info-section">
                <h2>📊 Información General</h2>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">
                    <div>
                        <p><strong>Número de Contrato:</strong> {cuenta['numero_contrato']}</p>
                        <p><strong>Número de Acta:</strong> {cuenta['numero_acta']}</p>
                        <p><strong>Contratista:</strong> {cuenta['contratista_nombre']}</p>
                    </div>
                    <div>
                        <p><strong>Valor:</strong> ${cuenta['valor']:,.0f}</p>
                        <p><strong>Estado Actual:</strong> 
                            <span style="background: #17a2b8; color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px;">
                                {cuenta['estado_actual'].replace('_', ' ').title()}
                            </span>
                        </p>
                        <p><strong>Responsable Actual:</strong> {cuenta.get('responsable_nombre', 'No asignado')}</p>
                        <p><strong>Fecha Radicación:</strong> {cuenta['timestamps']['radicacion']}</p>
                    </div>
                </div>
            </div>
            
            <div class="info-section">
                <h2>🕒 Historial y Comentarios</h2>
                {historial_html if historial_html else '<p>No hay historial registrado</p>'}
            </div>
        </div>
    </body>
    </html>
    '''

# ==================== DASHBOARD PRINCIPAL ====================
@app.route('/dashboard')
@login_required
def dashboard():
    user_rol = session['user_rol']
    user_nombre = session['user_nombre']
    
    cuentas = cargar_cuentas()
    
    # Filtrar cuentas según el rol
    if user_rol == 'contratista':
        mis_cuentas = [c for c in cuentas if c.get('contratista_id') == session['user_id']]
        cuentas_mostrar = mis_cuentas
    else:
        cuentas_mostrar = cuentas
    
    # Estadísticas básicas
    stats = {
        'total': len(cuentas_mostrar),
        'radicado': len([c for c in cuentas_mostrar if c['estado_actual'] == 'radicado']),
        'revision_epb': len([c for c in cuentas_mostrar if c['estado_actual'] == 'revision_epb']),
        'revision_supervisor': len([c for c in cuentas_mostrar if c['estado_actual'] == 'revision_supervisor']),
        'revision_general': len([c for c in cuentas_mostrar if c['estado_actual'] == 'revision_general']),
        'revision_hacienda': len([c for c in cuentas_mostrar if c['estado_actual'] == 'revision_hacienda']),
        'pagado': len([c for c in cuentas_mostrar if c['estado_actual'] == 'pagado']),
        'devuelto': len([c for c in cuentas_mostrar if c['estado_actual'] == 'devuelto'])
    }
    
    # Cuentas asignadas al usuario actual según su rol
    cuentas_asignadas = []
    if user_rol == 'epb':
        cuentas_asignadas = [c for c in cuentas if c['estado_actual'] == 'revision_epb']
    elif user_rol == 'supervisor':
        cuentas_asignadas = [c for c in cuentas if c['estado_actual'] == 'revision_supervisor']
    elif user_rol == 'general':
        cuentas_asignadas = [c for c in cuentas if c['estado_actual'] == 'revision_general']
    elif user_rol == 'hacienda':
        cuentas_asignadas = [c for c in cuentas if c['estado_actual'] == 'revision_hacienda']
    elif user_rol == 'contratista':
        cuentas_asignadas = [c for c in cuentas if c.get('contratista_id') == session['user_id'] and c['estado_actual'] == 'devuelto']
    
    # Cuentas pendientes de acción (para mostrar en el dashboard)
    cuentas_pendientes_html = ""
    if cuentas_asignadas:
        for cuenta in cuentas_asignadas[:5]:  # Mostrar máximo 5 cuentas
            estado_color = {
                'revision_epb': '#17a2b8', 
                'revision_supervisor': '#fd7e14',
                'revision_general': '#20c997',
                'revision_hacienda': '#6f42c1',
                'devuelto': '#dc3545'
            }.get(cuenta['estado_actual'], '#6c757d')
            
            cuentas_pendientes_html += f"""
            <div style="background: white; padding: 10px; margin: 5px 0; border-radius: 5px; border-left: 3px solid {estado_color};">
                <strong>{cuenta['numero_cuenta']}</strong>
                <div style="font-size: 12px; color: #666;">
                    {cuenta['contratista_nombre']} - ${cuenta['valor']:,.0f}
                </div>
                <a href="/cuentas" style="background: #007bff; color: white; padding: 3px 8px; text-decoration: none; border-radius: 3px; font-size: 11px; display: inline-block; margin-top: 5px;">
                    Ver detalles
                </a>
            </div>
            """
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Dashboard - {user_nombre}</title>
        <style>
            body {{ font-family: Arial; margin: 0; padding: 20px; background: #f5f5f5; }}
            .header {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin: 20px 0; }}
            .stat-card {{ background: white; padding: 15px; border-radius: 8px; text-align: center; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            .stat-number {{ font-size: 24px; font-weight: bold; margin: 10px 0; }}
            .nav {{ margin: 20px 0; }}
            .btn {{ background: #007bff; color: white; padding: 10px 15px; text-decoration: none; border-radius: 5px; margin-right: 10px; display: inline-block; }}
            .pendientes-section {{ background: white; padding: 20px; border-radius: 10px; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📊 Dashboard - {user_nombre}</h1>
            <p>Rol: <strong>{user_rol}</strong> | <a href="/logout">Cerrar sesión</a></p>
        </div>

        <div class="nav">
            <a href="/cuentas" class="btn">📋 Ver Cuentas</a>
            {'<a href="/radicar" class="btn">📝 Radicar Cuenta</a>' if user_rol == 'contratista' else ''}
            <a href="/usuarios" class="btn">👥 Usuarios</a>
        </div>

        {f'''
        <div class="pendientes-section">
            <h2>📌 Cuentas Pendientes de Mi Revisión</h2>
            <div class="stats">
                <div class="stat-card">
                    <div>Pendientes</div>
                    <div class="stat-number">{len(cuentas_asignadas)}</div>
                </div>
            </div>
            {cuentas_pendientes_html if cuentas_pendientes_html else '<p>No tienes cuentas pendientes de revisión</p>'}
            {f'<p><a href="/cuentas" class="btn">Ver todas las cuentas pendientes</a></p>' if cuentas_asignadas else ''}
        </div>
        ''' if user_rol != 'contratista' else ''}

        {f'''
        <div class="pendientes-section">
            <h2>📌 Cuentas Devueltas para Corrección</h2>
            <div class="stats">
                <div class="stat-card">
                    <div>Devueltas</div>
                    <div class="stat-number">{len(cuentas_asignadas)}</div>
                </div>
            </div>
            {cuentas_pendientes_html if cuentas_pendientes_html else '<p>No tienes cuentas devueltas</p>'}
            {f'<p><a href="/cuentas" class="btn">Ver cuentas devueltas</a></p>' if cuentas_asignadas else ''}
        </div>
        ''' if user_rol == 'contratista' and cuentas_asignadas else ''}

        <h2>📈 Resumen de Cuentas</h2>
        <div class="stats">
            <div class="stat-card">
                <div>Total</div>
                <div class="stat-number">{stats['total']}</div>
            </div>
            <div class="stat-card">
                <div>Radicado</div>
                <div class="stat-number">{stats['radicado']}</div>
            </div>
            <div class="stat-card">
                <div>Revisión EPB</div>
                <div class="stat-number">{stats['revision_epb']}</div>
            </div>
            <div class="stat-card">
                <div>Revisión Supervisor</div>
                <div class="stat-number">{stats['revision_supervisor']}</div>
            </div>
            <div class="stat-card">
                <div>Revisión General</div>
                <div class="stat-number">{stats['revision_general']}</div>
            </div>
            <div class="stat-card">
                <div>Revisión Hacienda</div>
                <div class="stat-number">{stats['revision_hacienda']}</div>
            </div>
            <div class="stat-card">
                <div>Pagado</div>
                <div class="stat-number">{stats['pagado']}</div>
            </div>
            <div class="stat-card">
                <div>Devuelto</div>
                <div class="stat-number">{stats['devuelto']}</div>
            </div>
        </div>
    </body>
    </html>
    '''

# ==================== GESTIÓN DE USUARIOS ====================
@app.route('/usuarios')
@login_required
@permiso_required('ver_todas')
def listar_usuarios():
    usuarios = cargar_usuarios()
    
    usuarios_html = ""
    for usuario in usuarios:
        rol_color = {
            'contratista': '#6c757d',
            'epb': '#007bff',
            'supervisor': '#fd7e14',
            'general': '#20c997',
            'hacienda': '#6f42c1'
        }.get(usuario['rol'], '#000')
        
        usuarios_html += f"""
        <div style="background: white; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid {rol_color}; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
            <h3 style="margin: 0 0 5px 0;">{usuario['nombre']}</h3>
            <p style="margin: 2px 0;"><strong>Usuario:</strong> {usuario['username']}</p>
            <p style="margin: 2px 0;"><strong>Rol:</strong> 
                <span style="background: {rol_color}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px;">
                    {usuario['rol']}
                </span>
            </p>
            <p style="margin: 2px 0;"><strong>Dependencia:</strong> {usuario.get('dependencia', 'No especificada')}</p>
            <p style="margin: 2px 0; font-size: 12px; color: #888;">Creado: {usuario['fecha_creacion']}</p>
        </div>
        """
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Usuarios del Sistema</title>
        <style>
            body {{ font-family: Arial; margin: 0; padding: 20px; background: #f5f5f5; }}
            .header {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .btn {{ background: #007bff; color: white; padding: 10px 15px; text-decoration: none; border-radius: 5px; margin-right: 10px; display: inline-block; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>👥 Usuarios del Sistema</h1>
            <div>
                <a href="/dashboard" class="btn">← Dashboard</a>
                <a href="/crear-usuario" class="btn" style="background: #28a745;">➕ Crear Usuario</a>
            </div>
        </div>
        
        {usuarios_html if usuarios_html else '<div style="background: white; padding: 30px; text-align: center; border-radius: 8px;"><p>No hay usuarios registrados</p></div>'}
    </body>
    </html>
    '''

# ==================== FUNCIÓN DE INICIALIZACIÓN ====================
def inicializar_sistema():
    """Crear algunos usuarios de ejemplo si no existen"""
    usuarios = cargar_usuarios()
    if not usuarios:
        usuarios_ejemplo = [
            {'id': 1, 'username': 'admin_epb', 'password': '123', 'rol': 'epb', 'nombre': 'Administrador EPB', 'dependencia': 'EPB', 'activo': True, 'fecha_creacion': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
            {'id': 2, 'username': 'contratista1', 'password': '123', 'rol': 'contratista', 'nombre': 'Empresa Constructora S.A.', 'dependencia': '', 'activo': True, 'fecha_creacion': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
            {'id': 3, 'username': 'supervisor1', 'password': '123', 'rol': 'supervisor', 'nombre': 'Supervisor Calidad', 'dependencia': 'Calidad', 'activo': True, 'fecha_creacion': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
            {'id': 4, 'username': 'general1', 'password': '123', 'rol': 'general', 'nombre': 'Secretaría General', 'dependencia': 'Secretaría General', 'activo': True, 'fecha_creacion': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
            {'id': 5, 'username': 'hacienda1', 'password': '123', 'rol': 'hacienda', 'nombre': 'Departamento Hacienda', 'dependencia': 'Hacienda', 'activo': True, 'fecha_creacion': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
            
        ]
        guardar_usuarios(usuarios_ejemplo)
        print("✅ Usuarios de ejemplo creados")

if __name__ == '__main__':
    inicializar_sistema()
    print("🚀 SISTEMA COMPLETO DE CUENTAS DE COBRO INICIADO")
    print("📍 Accede en: http://localhost:5000")
    print("👤 Usuario prueba: admin_epb / 123")
    app.run(host='0.0.0.0', port=5000, debug=True)

