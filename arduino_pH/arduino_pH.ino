/*
  Sistema integrado pH + Bomba seringa - Arduino Nano

  Pinos:
    Sensor pH:   A0
    TB6600 PUL+:  D7
    TB6600 DIR+:  D6
    TB6600 ENA+:  D5
    TB6600 PUL-, DIR-, ENA-: GND

  Driver: TB6600 em microstep 16 (3200 pulsos/volta), 1.5A
  Bomba: 0.42 voltas/ml (calibrado: 2.1 voltas = 5 ml)
  Seringa: limites de seguranca 1 a 19 ml
*/

#include <AccelStepper.h>

// ============ SENSOR DE pH ============
#define SensorPin A0

// Variáveis de Calibração do pH
float phGain = 3.5;    // Valor padrão de 'm' (ganho/slope)
float phOffset = -1.8; // Valor padrão de offset (intercepção)

int buf[10];
float phAtual = 0.0;
float tensaoAtual = 0.0;

// ============ BOMBA ============
const int PUL = 7;
const int DIR = 6;
const int ENA = 5;

const int PASSOS_POR_VOLTA = 3200;
const float VOLTAS_POR_ML = 2.1 / 5.0; // 0.42

const float VOLUME_MIN_ML = 1.0;
const float VOLUME_MAX_ML = 19.0;

const int SENTIDO_DISPENSA = -1;

AccelStepper stepper(AccelStepper::DRIVER, PUL, DIR);
float volumeAtual = 0.0;

// ============ CONTROLE DE TEMPO ============
unsigned long ultimaLeituraPH = 0;
const unsigned long INTERVALO_PH_MS = 1000; // le pH a cada 1s

// ============ CONTROLADOR PI + LINEARIZAÇÃO ============
bool piAtivo = false;
float piSetpoint = 7.0;      // pH alvo desejado
float piKp = 0.8;            // Ganho proporcional (Kp > 0)
float piKi = 0.2;            // Ganho integral (Ki > 0, 1/s)
float piErroAcumulado = 0.0; // Erro integral acumulado
unsigned long ultimoCicloPI = 0;
unsigned long piIntervaloMs = 1000; // Ciclo de controle a cada 1s

// Parâmetros Físicos do Reator
float V0 = 50.0; // Volume do reator (ml) - definido dinamicamente no setup
float pH_inicial = 6.5;   // pH inicial (definido dinamicamente ao ligar o PI)
const float C_HCl = 0.1;  // Concentração do ácido (M)
const float Kw = 2.4e-14; // Constante de dissociação da água

// Estado do Inversor
float u_max = 0.0;  // Volume máximo acumulado (trava de recuo)
float u_prev = 0.0; // Volume acumulado no passo anterior

// ============ LEITURA DE pH ============
float lerPH() {
  for (int i = 0; i < 10; i++) {
    buf[i] = analogRead(SensorPin);
    delay(10);
  }

  // ordena
  for (int i = 0; i < 9; i++) {
    for (int j = i + 1; j < 10; j++) {
      if (buf[i] > buf[j]) {
        int t = buf[i];
        buf[i] = buf[j];
        buf[j] = t;
      }
    }
  }

  // descarta os 2 maiores e 2 menores, media dos 6 do meio
  unsigned long soma = 0;
  for (int i = 2; i < 8; i++)
    soma += buf[i];

  // Calcula a tensao e salva na variavel global
  tensaoAtual = (float)soma * 5.0 / 1024.0 / 6.0;

  // Aplica o ganho e o offset variáveis
  float phValue = phGain * tensaoAtual + phOffset;

  return phValue;
}

// ============ LINEARIZADOR (INVERSOR) ============
float calcularInversor(float v) {
  // 1. Ajusta o pH alvo com o pH inicial
  float pH_alvo = v + pH_inicial;

  // --- TRAVA DE SANIDADE DO SINAL ---
  // Clampa o pH alvo para faixas fisicamente realistas
  if (pH_alvo < 1.0)
    pH_alvo = 1.0;
  if (pH_alvo > 14.0)
    pH_alvo = 14.0;

  // 2. Calcula W0 (inicial)
  float H_ini = pow(10.0, -pH_inicial);
  float W0 = H_ini - (Kw / H_ini);

  // 3. Inversa do tampão (alvo)
  float H_alvo = pow(10.0, -pH_alvo);
  float W_alvo = H_alvo - (Kw / H_alvo);

  // Evita divisão por zero/negativa se W_alvo for muito próximo ou maior que
  // C_HCl
  if (W_alvo >= C_HCl * 0.99) {
    W_alvo = C_HCl * 0.99;
  }

  // 4. Inversa da diluição: Calcula o volume acumulado de ácido necessário
  float u_calc = V0 * (W_alvo - W0) / (C_HCl - W_alvo);

  // 5. Trava de recuo (Saturação Física da Bomba)
  float u_out;
  if (u_calc < u_max) {
    u_out = u_max;
  } else {
    u_out = u_calc;
    u_max = u_calc;
  }

  return u_out;
}

// ============ LOGICA DO CONTROLADOR PI ============
void processarControladorPI() {
  if (!piAtivo)
    return;

  if (millis() - ultimoCicloPI >= piIntervaloMs) {
    unsigned long agora = millis();
    float dt = (float)(agora - ultimoCicloPI) / 1000.0;
    ultimoCicloPI = agora;

    // erro = Setpoint - pH (conforme Simulink: negativo se pH > Setpoint)
    float erro = piSetpoint - phAtual;

    // Proporcional
    float pTerm = piKp * erro;

    // Integral (anti-windup: só acumula se erro for negativo, ou seja, pH >
    // Setpoint)
    if (erro < 0.0) {
      piErroAcumulado += erro * dt;
    } else {
      // Se já passou do setpoint, zera o erro acumulado para evitar overshoot
      // de recuo
      piErroAcumulado = 0.0;
    }

    float iTerm = piKi * piErroAcumulado;

    // Saída do PI (v)
    float v = pTerm + iTerm;

    // Calcula o volume acumulado absoluto de ácido requerido pelo inversor
    float u_out = calcularInversor(v);

    // A dose incremental a injetar neste ciclo
    float mlDose = u_out - u_prev;

    Serial.print(F("[PI] pH: "));
    Serial.print(phAtual, 2);
    Serial.print(F(" | SP: "));
    Serial.print(piSetpoint, 2);
    Serial.print(F(" | Erro: "));
    Serial.print(erro, 2);
    Serial.print(F(" | v: "));
    Serial.print(v, 3);
    Serial.print(F(" | u_out: "));
    Serial.print(u_out, 3);
    Serial.print(F(" | Dose: "));
    Serial.print(mlDose, 3);
    Serial.println(F(" ml"));

    if (mlDose >= 0.001) {
      // Verifica se a seringa tem volume disponível para dispensar
      if (volumeAtual - mlDose < VOLUME_MIN_ML) {
        Serial.println(
            F("[PI] AVISO: Seringa atingiu o limite minimo! Desativando PI."));
        piAtivo = false;
        // Dosar o máximo restante com segurança
        float doseRestante = volumeAtual - VOLUME_MIN_ML;
        if (doseRestante > 0.01) {
          moverML(doseRestante);
        }
      } else {
        moverML(mlDose);
        u_prev = u_out; // Atualiza o u_prev apenas se a dose foi efetuada com
                        // sucesso
      }
    }
  }
}

// ============ MOVIMENTO POR ML ============
void moverML(float mlSolicitado) {
  if (mlSolicitado == 0)
    return;

  float volumeFinal = volumeAtual - mlSolicitado;

  if (volumeFinal < VOLUME_MIN_ML) {
    Serial.print(F("ERRO: dispensar "));
    Serial.print(mlSolicitado, 2);
    Serial.print(F(" ml deixaria a seringa em "));
    Serial.print(volumeFinal, 2);
    Serial.print(F(" ml (abaixo de "));
    Serial.print(VOLUME_MIN_ML, 1);
    Serial.println(F(" ml)."));
    Serial.print(F("Maximo dispensavel agora: "));
    Serial.print(volumeAtual - VOLUME_MIN_ML, 2);
    Serial.println(F(" ml"));
    return;
  }
  if (volumeFinal > VOLUME_MAX_ML) {
    Serial.print(F("ERRO: aspirar "));
    Serial.print(-mlSolicitado, 2);
    Serial.print(F(" ml ultrapassaria "));
    Serial.print(VOLUME_MAX_ML, 1);
    Serial.println(F(" ml."));
    Serial.print(F("Maximo aspiravel agora: "));
    Serial.print(VOLUME_MAX_ML - volumeAtual, 2);
    Serial.println(F(" ml"));
    return;
  }

  float voltas = mlSolicitado * VOLTAS_POR_ML;
  long passos = (long)(voltas * PASSOS_POR_VOLTA) * SENTIDO_DISPENSA;
  long posicaoAntes = stepper.currentPosition();

  Serial.print(F("Movendo "));
  Serial.print(mlSolicitado, 2);
  Serial.print(F(" ml | "));
  Serial.print(voltas, 3);
  Serial.print(F(" voltas | "));
  Serial.print(abs(passos));
  Serial.println(F(" passos..."));

  stepper.move(passos);
  while (stepper.distanceToGo() != 0)
    stepper.run();

  long posicaoDepois = stepper.currentPosition();
  long passosExecutados = posicaoDepois - posicaoAntes;

  volumeAtual = volumeFinal;

  Serial.print(F("OK | Seringa: "));
  Serial.print(volumeAtual, 2);
  Serial.print(F(" ml | Passos enviados: "));
  Serial.print(passosExecutados);
  Serial.print(F(" | Posicao acumulada: "));
  Serial.println(posicaoDepois);

  if (volumeAtual <= VOLUME_MIN_ML + 0.5) {
    Serial.println(F(">> Atencao: proximo do limite inferior"));
  } else if (volumeAtual >= VOLUME_MAX_ML - 0.5) {
    Serial.println(F(">> Atencao: proximo do limite superior"));
  }
}

// ============ MOVIMENTO POR PASSOS ============
void moverPassos(long passosSolicitados) {
  if (passosSolicitados == 0)
    return;

  // converte passos em ml equivalente pra checar limites
  float voltasEquiv = (float)passosSolicitados / PASSOS_POR_VOLTA;
  float mlEquiv = voltasEquiv / VOLTAS_POR_ML;

  float volumeFinal = volumeAtual - mlEquiv;

  if (volumeFinal < VOLUME_MIN_ML) {
    Serial.print(F("ERRO: "));
    Serial.print(passosSolicitados);
    Serial.print(F(" passos (~"));
    Serial.print(mlEquiv, 2);
    Serial.print(F(" ml) deixaria seringa em "));
    Serial.print(volumeFinal, 2);
    Serial.println(F(" ml."));
    return;
  }
  if (volumeFinal > VOLUME_MAX_ML) {
    Serial.print(F("ERRO: "));
    Serial.print(passosSolicitados);
    Serial.print(F(" passos (~"));
    Serial.print(-mlEquiv, 2);
    Serial.print(F(" ml aspiracao) ultrapassaria "));
    Serial.print(VOLUME_MAX_ML, 1);
    Serial.println(F(" ml."));
    return;
  }

  long passosReais = passosSolicitados * SENTIDO_DISPENSA;
  long posicaoAntes = stepper.currentPosition();

  Serial.print(F("Movendo "));
  Serial.print(passosSolicitados);
  Serial.print(F(" passos (~"));
  Serial.print(mlEquiv, 3);
  Serial.println(F(" ml)..."));

  stepper.move(passosReais);
  while (stepper.distanceToGo() != 0)
    stepper.run();

  long posicaoDepois = stepper.currentPosition();
  long passosExecutados = posicaoDepois - posicaoAntes;

  volumeAtual = volumeFinal;

  Serial.print(F("OK | Seringa: "));
  Serial.print(volumeAtual, 2);
  Serial.print(F(" ml | Passos enviados: "));
  Serial.print(passosExecutados);
  Serial.print(F(" | Posicao acumulada: "));
  Serial.println(posicaoDepois);
}

// ============ SETUP ============
void setup() {
  Serial.begin(9600);
  delay(300);

  pinMode(ENA, OUTPUT);
  digitalWrite(ENA, LOW); // habilita driver (LOW = ativo)

  stepper.setMaxSpeed(3200);
  stepper.setAcceleration(3200);
  stepper.setCurrentPosition(0);

  Serial.println(F("\n=== Sistema pH + Bomba (Nano) ==="));
  Serial.println(F("Calibracao bomba: 0.42 voltas/ml | 3200 passos/volta"));
  Serial.print(F("Limites seringa: "));
  Serial.print(VOLUME_MIN_ML, 1);
  Serial.print(F(" a "));
  Serial.print(VOLUME_MAX_ML, 1);
  Serial.println(F(" ml\n"));

  Serial.print(F("Volume inicial na seringa (ml)? "));
  while (Serial.available() == 0) {
    delay(10);
  }
  volumeAtual = Serial.parseFloat();
  while (Serial.available() > 0)
    Serial.read();

  if (volumeAtual < VOLUME_MIN_ML || volumeAtual > VOLUME_MAX_ML) {
    Serial.print(F("AVISO: volume "));
    Serial.print(volumeAtual, 2);
    Serial.println(F(" ml fora da faixa segura!"));
  }

  Serial.print(F("Volume registrado: "));
  Serial.print(volumeAtual, 2);
  Serial.println(F(" ml"));

  Serial.print(F("Volume do reator V0 (ml)? "));
  while (Serial.available() == 0) {
    delay(10);
  }
  V0 = Serial.parseFloat();
  while (Serial.available() > 0)
    Serial.read();

  Serial.print(F("Volume V0 registrado: "));
  Serial.print(V0, 2);
  Serial.println(F(" ml"));

  // primeira leitura de pH
  phAtual = lerPH();
  Serial.print(F("pH inicial: "));
  Serial.println(phAtual, 2);

  Serial.print(F("Posicao motor: "));
  Serial.print(stepper.currentPosition());
  Serial.println(F(" passos"));
  Serial.println(F("--------------------------------------"));
}

// ============ LOOP ============
void loop() {
  // Leitura periodica e IMPRESSÃO (a cada 1 segundo)
  if (millis() - ultimaLeituraPH >= INTERVALO_PH_MS) {
    phAtual = lerPH();
    ultimaLeituraPH = millis();

    Serial.print(F("pH:"));
    Serial.println(phAtual, 2);
    Serial.print(F("Tensao:"));
    Serial.println(tensaoAtual, 3);
  }

  // Processa a ação de controle PI se ativo
  processarControladorPI();

  // Processa comandos do Serial
  if (Serial.available() > 0) {
    delay(20);
    String linha = Serial.readStringUntil('\n');
    linha.trim();

    if (linha.length() == 0)
      return;

    String linhaLower = linha;
    linhaLower.toLowerCase();

    // Processamento de comandos
    if (linhaLower.startsWith("ml:")) {
      float ml = linha.substring(3).toFloat();
      moverML(ml);
    } else if (linhaLower.startsWith("step:")) {
      long passos = linha.substring(5).toInt();
      moverPassos(passos);
    }
    // Novos comandos de calibração de pH
    else if (linhaLower.startsWith("offset:")) {
      phOffset = linha.substring(7).toFloat();
      Serial.print(F(">>> Novo Offset de pH definido: "));
      Serial.println(phOffset, 3);
    } else if (linhaLower.startsWith("gain:")) {
      phGain = linha.substring(5).toFloat();
      Serial.print(F(">>> Novo Ganho (m) definido: "));
      Serial.println(phGain, 3);
    }
    // Comandos do Controlador PI
    else if (linhaLower == "pi:on") {
      piAtivo = true;
      ultimoCicloPI = millis(); // Inicializa temporização

      // Atualiza o pH inicial dinamicamente com a leitura atual do sensor
      phAtual = lerPH();
      pH_inicial = phAtual;

      // Reseta o estado acumulado do inversor e do integrador
      piErroAcumulado = 0.0;
      u_max = 0.0;
      u_prev = 0.0;

      Serial.print(
          F(">>> Controlador PI ativado. pH inicial calibrado para: "));
      Serial.println(pH_inicial, 2);
    } else if (linhaLower == "pi:off") {
      piAtivo = false;
      Serial.println(F(">>> Controlador PI desativado."));
    } else if (linhaLower.startsWith("sp:")) {
      piSetpoint = linha.substring(3).toFloat();
      Serial.print(F(">>> Setpoint do PI definido para: "));
      Serial.println(piSetpoint, 2);
    } else if (linhaLower.startsWith("kp:")) {
      piKp = linha.substring(3).toFloat();
      Serial.print(F(">>> Novo Kp do PI: "));
      Serial.println(piKp, 4);
    } else if (linhaLower.startsWith("ki:")) {
      piKi = linha.substring(3).toFloat();
      Serial.print(F(">>> Novo Ki do PI: "));
      Serial.println(piKi, 4);
    } else if (linhaLower.startsWith("piint:")) {
      piIntervaloMs = linha.substring(6).toInt();
      Serial.print(F(">>> Novo intervalo do PI (ms): "));
      Serial.println(piIntervaloMs);
    } else if (linhaLower == "zpi") {
      piErroAcumulado = 0.0;
      u_max = 0.0;
      u_prev = 0.0;
      Serial.println(
          F(">>> Estado do PI resetado (erro acumulado e volumes zerados)."));
    } else if (linhaLower.startsWith("v0:")) {
      float novoV0 = linha.substring(3).toFloat();
      if (novoV0 > 0.0) {
        V0 = novoV0;
        Serial.print(F(">>> Volume do reator V0 atualizado para: "));
        Serial.print(V0, 2);
        Serial.println(F(" ml"));
      } else {
        Serial.println(F("Valor de V0 invalido."));
      }
    } else if (linhaLower == "piinfo") {
      Serial.println(F("=== Parametros e Estado PI ==="));
      Serial.print(F("Ativo: "));
      Serial.println(piAtivo ? F("SIM") : F("NAO"));
      Serial.print(F("Setpoint (SP): "));
      Serial.println(piSetpoint, 2);
      Serial.print(F("pH atual: "));
      Serial.println(phAtual, 2);
      Serial.print(F("Volume Reator V0: "));
      Serial.print(V0, 2);
      Serial.println(F(" ml"));
      Serial.print(F("Kp: "));
      Serial.println(piKp, 4);
      Serial.print(F("Ki: "));
      Serial.println(piKi, 4);
      Serial.print(F("Intervalo (ms): "));
      Serial.println(piIntervaloMs);
      Serial.print(F("Erro acumulado (I): "));
      Serial.println(piErroAcumulado, 4);
      Serial.print(F("u_max: "));
      Serial.println(u_max, 4);
      Serial.print(F("u_prev: "));
      Serial.println(u_prev, 4);
      Serial.println(F("------------------------------"));
    }
    // Outros comandos
    else if (linhaLower == "v") {
      Serial.print(F("Volume na seringa: "));
      Serial.print(volumeAtual, 2);
      Serial.println(F(" ml"));
    } else if (linhaLower == "s") {
      long pos = stepper.currentPosition();
      Serial.print(F("Posicao acumulada: "));
      Serial.print(pos);
      Serial.println(F(" passos"));
    } else if (linhaLower == "z") {
      stepper.setCurrentPosition(0);
      Serial.println(F("Contador de passos zerado."));
    } else if (linhaLower.startsWith("r ")) {
      float novoVol = linha.substring(2).toFloat();
      if (novoVol >= 0 && novoVol <= 20) {
        volumeAtual = novoVol;
        Serial.print(F("Volume redefinido para "));
        Serial.print(volumeAtual, 2);
        Serial.println(F(" ml"));
      } else {
        Serial.println(F("Valor invalido (0 a 20)"));
      }
    } else {
      Serial.println(
          F("Comando desconhecido. Use: 'ml:<val>', 'step:<val>', "
            "'offset:<val>', 'gain:<val>', 'v', 's', 'z', 'r <val>', 'pi:on', "
            "'pi:off', 'sp:<val>', 'kp:<val>', 'ki:<val>', 'piint:<val>', "
            "'zpi', 'piinfo', 'v0:<val>'"));
    }
  }
}
