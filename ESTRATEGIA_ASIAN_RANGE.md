# Estrategia: Asian Range Breakout (Gold / XAUUSD)

## Resumen

Estrategia de breakout que opera Gold (XAUUSD) una vez al dia. Se basa en un patron bien documentado: el precio del oro se consolida durante la sesion asiatica (Tokyo) y luego rompe con fuerza cuando abre Londres.

El bot marca el maximo y minimo de la sesion asiatica, y cuando el precio rompe alguno de esos niveles despues de la apertura de Londres, entra en la direccion del breakout.

---

## Como funciona paso a paso

### 1. Sesion asiatica (7:00 PM - 2:00 AM Colombia / 00:00-07:00 UTC)

El bot observa las velas de 1 minuto y registra:
- **HIGH**: el precio mas alto de la sesion
- **LOW**: el precio mas bajo de la sesion

Esto forma el "rango asiatico". Necesita al menos 30 velas (~30 minutos) para considerarlo valido.

### 2. Filtros de rango

Antes de buscar breakout, el bot verifica:

| Filtro | Condicion | Razon |
|--------|-----------|-------|
| Rango muy ancho | > $25 | Dia muy volatil, el stop seria enorme |
| Rango muy angosto | < $2 | No hay estructura clara, ruido |

Si el rango no pasa los filtros, no opera ese dia.

### 3. Ventana de breakout (3:00 AM - 7:00 AM Colombia / 08:00-12:00 UTC)

Cuando abre Londres, el bot compara el precio actual con el rango:

```
Precio > Asian HIGH  -->  COMPRA (BUY)
Precio < Asian LOW   -->  VENTA (SELL)
Precio dentro del rango  -->  No opera
```

### 4. Stop Loss y Take Profit

| Parametro | Valor | Ejemplo (rango $10) |
|-----------|-------|---------------------|
| **Stop Loss** | Extremo opuesto del rango | BUY: SL en el LOW del rango |
| **Take Profit** | 1.5x el ancho del rango | TP = $15 de distancia |

Ambos son niveles absolutos enviados a Capital.com al abrir la posicion. El broker los ejecuta server-side, asi que la posicion esta protegida aunque el bot se desconecte.

### 5. Maximo 1 trade por dia

Una vez que el bot entra (BUY o SELL), no vuelve a operar hasta el dia siguiente. Esto previene overtrading en dias de fake breakouts.

---

## Ejemplo real

```
Dia: Lunes 31 de marzo 2026

Sesion asiatica (7:00 PM - 2:00 AM Colombia):
  HIGH = $3,125.50
  LOW  = $3,115.20
  Ancho del rango = $10.30

8:15 AM UTC (3:15 AM Colombia):
  Precio actual = $3,126.80 (rompio el HIGH)

  --> COMPRA (BUY)
  Entry:  $3,126.80
  SL:     $3,115.20  (LOW del rango, -$11.60)
  TP:     $3,142.25  (1.5x rango = +$15.45)

Resultado posible:
  - Si llega al TP: +$15.45 por contrato
  - Si toca el SL: -$11.60 por contrato
  - Risk/Reward: 1:1.33
```

---

## Cuenta y presupuesto

| Detalle | Valor |
|---------|-------|
| Cuenta Capital.com | `asian_range` |
| Account ID | `315701137306366238` |
| Presupuesto | $1,000 |
| Instrumento | GOLD (XAUUSD) |
| Riesgo por trade | ~2% ($20 max) |
| Contratos | 0.5 - 1 mini lot |

La cuenta es independiente de la cuenta `microprofits` (US100 scalper). Cada una tiene su propia sesion API y tracker de posiciones.

---

## Horarios (Colombia, UTC-5)

| Fase | Hora Colombia | Hora UTC | Que hace el bot |
|------|--------------|----------|-----------------|
| Rango asiatico | 7:00 PM - 2:00 AM | 00:00 - 07:00 | Observa y registra HIGH/LOW |
| Pausa | 2:00 AM - 3:00 AM | 07:00 - 08:00 | Espera apertura de Londres |
| Breakout | 3:00 AM - 7:00 AM | 08:00 - 12:00 | Busca entrada si hay breakout |
| Inactivo | 7:00 AM - 7:00 PM | 12:00 - 00:00 | No opera Gold |

**No necesitas tener el PC prendido** — el bot corre en el servidor AWS Lightsail 24/7.

---

## Matematicas esperadas

Basado en el comportamiento historico del oro en sesiones asiaticas:

```
Trades por mes:         ~15-18 (no todos los dias hay breakout limpio)
Win rate esperado:      55-60%
Rango promedio del oro: $8-15 por dia

Con 0.5 contratos y 57% win rate:
  Ganadores: 10 x $12 avg = $120
  Perdedores: 7.5 x $9 avg = $67.50
  Neto mensual: ~$52.50 (5.25% sobre $1,000)

En 6 meses con compounding:
  $1,000 -> ~$1,360
```

Estas son estimaciones conservadoras. El resultado real depende de la volatilidad del mercado.

---

## Riesgos

1. **Fake breakouts**: el precio rompe el rango, entra, y se devuelve. El SL limita la perdida.
2. **Dias sin rango**: si el rango es < $2 o > $25, el bot no opera (proteccion integrada).
3. **Gaps de fin de semana**: Gold no opera sabado/domingo, puede abrir con gap el lunes.
4. **Spread**: el spread de Gold en Capital.com es ~$0.30-0.50, incluido en el precio de entrada.

---

## Donde ver el estado

- **Dashboard**: `http://13.41.3.104:3000`
  - Panel "Strategy Schedule" muestra si la estrategia esta activa
  - Panel "Symbols" muestra GOLD con estrategia "Asian Range" y estado ON/OFF
  - Tabla de posiciones muestra trades abiertos en tiempo real
  - Historial de trades muestra P&L de trades cerrados

- **Logs del servidor**:
  ```bash
  ssh ubuntu@100.101.111.35 "docker logs microprofits-backend --tail 50"
  ```

- **API directa**:
  ```bash
  # Estado del bot
  curl http://13.41.3.104:8000/api/status

  # Posiciones abiertas
  curl http://13.41.3.104:8000/api/positions

  # Historial de trades
  curl http://13.41.3.104:8000/api/trades

  # Config de simbolos
  curl http://13.41.3.104:8000/api/config/symbols
  ```

---

## Archivos clave del codigo

| Archivo | Que hace |
|---------|----------|
| `backend/microprofits/strategy/asian_range.py` | Logica de la estrategia (rango + breakout + filtros) |
| `backend/microprofits/engine/loop.py` | Loop principal, rutea a la estrategia correcta por simbolo |
| `backend/microprofits/data/store.py` | Base de datos, tabla `symbol_config` con strategy + account_id |
| `backend/tests/test_asian_range.py` | 9 tests unitarios de la estrategia |
| `frontend/src/components/SchedulePanel.tsx` | Panel de horarios en el dashboard |
