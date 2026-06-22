# Simulador Digestivo — Controle de pH Estomacal
**ECA-UFSC-FLN / Semestre 2026.1 - Grupo G1**

Este repositório contém o sistema de controle de pH para o **Simulador de Sistema Digestivo**. O projeto é composto por um firmware desenvolvido para **Arduino Nano** (responsável pela leitura de sensores, acionamento mecânico e lógica de controle) e uma **Interface Homem-Máquina (IHM)** desenvolvida em **Python** (responsável pelo monitoramento gráfico, configuração de calibrações, sintonia de controle e exportação de dados).

A comunicação entre a IHM e o hardware é realizada via **Porta Serial (USB)**.

---

## 📌 Estrutura do Projeto

*   **[`arduino_pH/`](file:///Users/paulino/projects/controle_ph/arduino_pH)**: Contém o código do firmware do microcontrolador.
    *   [`arduino_pH.ino`](file:///Users/paulino/projects/controle_ph/arduino_pH/arduino_pH.ino): Código principal que executa a leitura do sensor de pH, a malha de controle PI com inversor químico, a movimentação do motor de passo e a interpretação de comandos seriais.
*   **[`IHM/`](file:///Users/paulino/projects/controle_ph/IHM)**: Contém a interface gráfica.
    *   [`main.py`](file:///Users/paulino/projects/controle_ph/IHM/main.py): Código-fonte da aplicação de monitoramento baseada em `CustomTkinter`.
    *   [`requirements.txt`](file:///Users/paulino/projects/controle_ph/IHM/requirements.txt): Relação de dependências do Python.
    *   [`calibrations.db`](file:///Users/paulino/projects/controle_ph/IHM/calibrations.db): Banco de dados SQLite local usado para armazenar e carregar históricos de calibração do sensor.
    *   [`run.command`](file:///Users/paulino/projects/controle_ph/IHM/run.command): Script Bash para execução simplificada no macOS/Linux.
    *   [`run.bat`](file:///Users/paulino/projects/controle_ph/IHM/run.bat): Script Batch para execução simplificada no Windows.

---

## 🔌 Hardware e Conexões

O sistema é centrado em um **Arduino Nano** atuando como controlador de processos, comandando uma **Bomba de Seringa** e lendo dados de um **Sensor de pH analógico**.

### Pinagem do Arduino

| Componente | Pino Arduino | Descrição |
| :--- | :--- | :--- |
| **Sensor de pH** | `A0` | Sinal analógico de tensão do sensor de pH |
| **TB6600 PUL+** | `D7` | Sinal de pulso do motor de passo |
| **TB6600 DIR+** | `D6` | Sinal de direção do motor de passo |
| **TB6600 ENA+** | `D5` | Sinal de habilitação do driver (Ativo em nível lógico `LOW`) |
| **TB6600 PUL-, DIR-, ENA-** | `GND` | Todos conectados ao Terra de referência do Arduino |

### Parâmetros da Bomba de Seringa
*   **Driver do Motor (TB6600)**: Configurado em micropasso **16** (resultando em **3200 passos/volta**) com limite de corrente em **1.5A**.
*   **Fator de Calibração Mecânico**: $0.42\text{ voltas/ml}$ (calibrado como $2.1\text{ voltas} = 5\text{ ml}$ de fluido).
*   **Limites de Segurança**: Limites físicos e de volume impostos entre $1.0\text{ ml}$ (mínimo) e $19.0\text{ ml}$ (máximo) para proteção física da seringa e motor contra fim de curso.

---

## 🧠 Estratégia de Controle

O controle de pH estomacal é não-linear por natureza devido à escala logarítmica do pH e às propriedades físicas do equilíbrio químico do fluido. Para obter uma resposta precisa, o sistema implementa:

### 1. Filtro Digital do Sensor de pH
Para suavizar ruídos analógicos de alta frequência do sensor de pH, a leitura analógica no pino `A0` realiza a amostragem de **10 medições consecutivas**, ordena o vetor de amostras, descarta as duas maiores e as duas menores leituras e calcula a média das 6 amostras intermediárias restantes. A tensão resultante é convertida em pH por meio de uma equação linear clássica:
$$\text{pH} = m \cdot V_{\text{sensor}} + c$$
*(Onde $m$ é o ganho e $c$ é o offset, configurados no firmware e calibrados via IHM).*

### 2. Controlador PI + Linearizador Químico (Inversor)
O algoritmo no Arduino calcula o esforço de controle com base na variação do pH do reator (representando o estômago).
*   **Controlador PI**: Calcula o erro em relação ao Setpoint de pH desejado.
    *   Para evitar *overshoot* e acúmulo indesejado no integrador caso o pH passe do ponto (já que a bomba só consegue dosar reagente ácido para reduzir o pH e não pode aspirar em malha fechada), o termo integrador possui um mecanismo de **Anti-Windup** que zera o erro acumulado quando o pH está abaixo do setpoint.
*   **Linearizador Químico**: A saída de sinal linear do controlador PI ($v$) é convertida em dose física de ácido volumétrico ($\text{ml}$) utilizando a modelagem do reator:
    *   Considera o volume inicial do estômago ($V_0$).
    *   Considera a concentração do ácido clorídrico injetado ($C_{\text{HCl}} = 0.1\text{ M}$).
    *   Utiliza a constante de dissociação da água ($K_w = 2.4 \times 10^{-14}$) para computar a concentração de íons $H^+$ requerida e determinar a variação volumétrica exata de ácido acumulado a ser injetada.

---

## 💬 Protocolo de Comunicação Serial

A IHM e o Arduino se comunicam por texto plano pela porta serial (Baud Rate padrão: **9600 bps**). 

### Comandos Enviados para o Arduino
O Arduino processa comandos finalizados com quebra de linha `\n` (insensíveis a maiúsculas/minúsculas):

*   `ml:<val>`: Desloca o êmbolo da seringa para dispensar (se valor positivo) ou aspirar (se valor negativo) a quantidade exata em mililitros.
*   `step:<val>`: Desloca o motor de passo pela quantidade exata de passos indicada.
*   `offset:<val>`: Altera o valor de offset ($c$) da curva de calibração do sensor de pH.
*   `gain:<val>`: Altera o valor do ganho ($m$) da curva de calibração do sensor de pH.
*   `v`: Retorna o volume atual estimado restante na seringa (`Volume na seringa: X.XX ml`).
*   `s`: Retorna a posição acumulada do motor em passos.
*   `z`: Zera o contador de passos do motor.
*   `r <val>`: Redefine via software o volume inicial carregado na seringa.
*   `pi:on` / `pi:off`: Ativa ou desativa a execução automática da malha fechada de controle PI.
*   `sp:<val>`: Define o setpoint de pH desejado (ex: `sp:3.5`).
*   `kp:<val>` / `ki:<val>`: Ajusta os ganhos proporcional e integral do controlador.
*   `piint:<val>`: Define o intervalo do loop de controle PI em milissegundos.
*   `v0:<val>`: Define o volume total atual do reator $V_0$.
*   `piinfo`: Solicita o dump de todas as variáveis internas e estados do controlador PI.
*   `zpi`: Zera a integral acumulada e o histórico de vazão do integrador químico.

### Mensagens Enviadas pelo Arduino (Telemetria)
A cada 1 segundo, o Arduino envia dados de telemetria no formato `Chave:Valor`:
*   `pH:X.XX` — Leitura filtrada de pH atual.
*   `Tensao:X.XXX` — Leitura de tensão em Volts no pino A0.

Além disso, mensagens de depuração ou requisições de configuração (como as perguntas de inicialização de volumes) são enviadas em texto aberto e terminadas com `?`, fazendo com que a IHM abra janelas de diálogo interativas.

---

## 💻 Interface Homem-Máquina (IHM)

A IHM foi construída em Python utilizando as seguintes bibliotecas principais:
1.  **`CustomTkinter`**: Para uma interface moderna com suporte nativo a temas escuros.
2.  **`Matplotlib`**: Para plotagem em tempo real das curvas de pH.
3.  **`PySerial`**: Para gerenciar a conexão USB.
4.  **`SQLite3`**: Banco de dados embarcado para histórico de calibração do sensor.

### Funcionalidades
*   **Conexão Automática**: Detecta portas seriais USB ativas e busca dispositivos compatíveis com chips seriais comumente usados em Arduino (ex: CH340, FT232, CP210, etc.).
*   **Gráfico em Tempo Real**: Plota a evolução temporal do pH do simulador.
*   **Controle de Modos**: Permite alternar entre **Controle Manual** (injeção direta de passos ou ml) e **Controle Automático** (onde a malha fechada calcula as doses via PI).
*   **Gerenciador de Calibração**: Uma rotina guiada passo a passo para calibrar o sensor utilizando duas soluções de referência (geralmente pH 4.0 e pH 7.0). O ganho e offset calculados são armazenados no banco local `calibrations.db` com descrição histórica e enviados ao firmware.
*   **Gravação de CSV**: Captura dados a cada $100\text{ ms}$ (pH, tensão, volume dosado) e exporta em formato de planilha para posterior análise acadêmica ou relatórios.
*   **Console de Depuração**: Área expansível para visualizar strings brutas de envio e recebimento de comandos seriais.

---

## 🚀 Como Executar o Projeto

### 1. Configurando o Arduino
1.  Conecte o Arduino Nano ao computador via cabo USB.
2.  Abra a Arduino IDE.
3.  Instale a biblioteca **`AccelStepper`** através do Gerenciador de Bibliotecas da IDE.
4.  Abra o arquivo [`arduino_pH/arduino_pH.ino`](file:///Users/paulino/projects/controle_ph/arduino_pH/arduino_pH.ino).
5.  Selecione a placa **Arduino Nano** e a porta correspondente.
6.  Realize o upload do código para a placa.

### 2. Executando a IHM Python

Os scripts fornecidos automatizam a criação de um ambiente virtual Python (`venv`) e instalam as dependências sem afetar as configurações globais de sua máquina.

#### No macOS ou Linux:
1.  Abra o terminal na pasta raiz do projeto.
2.  Dê permissão de execução para o script (necessário apenas uma vez):
    ```bash
    chmod +x IHM/run.command
    ```
3.  Execute o script:
    ```bash
    ./IHM/run.command
    ```

#### No Windows:
1.  Dê dois cliques no arquivo [`run.bat`](file:///Users/paulino/projects/controle_ph/IHM/run.bat) na pasta `IHM`.
2.  O prompt de comando criará o ambiente virtual local, instalará as dependências listadas em `requirements.txt` e iniciará a aplicação automaticamente.

*Nota: Se preferir rodar manualmente na linha de comando sem usar os scripts automatizados, execute:*
```bash
pip install -r IHM/requirements.txt
python IHM/main.py
```

---

## 🛠️ Utilização Passo a Passo

1.  **Inicialização**: Ao abrir a IHM, clique em **Iniciar Conexão**. O sistema tentará se conectar ao Arduino.
2.  **Calibração Inicial (Perguntas)**: Na inicialização da conexão serial, o Arduino solicitará através de caixas de diálogo na IHM:
    *   O volume atual carregado fisicamente na seringa (em ml).
    *   O volume do reator $V_0$ do estômago (em ml).
3.  **Calibração do pH**: Se o sensor necessitar de ajuste, clique no botão roxo **Calibrar pH** e siga a rotina interativa de calibração em dois pontos (pH 4 e pH 7).
4.  **Operação**:
    *   No **Modo Manual**, digite quantos ml ou passos deseja movimentar e clique em **SET**.
    *   Para o **Modo Automático**, ative a chave "Contr. Automático", configure o **pH alvo** no visor e clique em **SET**. A malha do PI passará a atuar dinamicamente enviando microdoses de ácido à medida que o pH sobe.
5.  **Coleta de Dados**: Clique em **Iniciar Gravação CSV** para armazenar o log temporal da sua simulação. Quando finalizar, clique em **Parar e Salvar CSV** para selecionar a pasta de destino do arquivo.
