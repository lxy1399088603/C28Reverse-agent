IDA2C28X_RULES = """

### 参数识别

可传递参数的寄存器：

| 寄存器 | 数据宽度 | 典型用途 |
|--------|----------|----------|
| AL | 16-bit 定点 | 整数参数 (Uint16 / int16) |
| AH | 16-bit 定点 | 整数参数 |
| ACC (AH:AL) | 32-bit 定点 | 长整数参数 (Uint32 / int32) |
| XAR4 | 32-bit 地址 | 指针参数，或 32-bit 整数 |
| XAR5 | 32-bit 地址 | 指针参数，或 32-bit 整数 |
| AR4 | 16-bit 定点 | 整数参数（XAR4 的低 16 位） |
| AR5 | 16-bit 定点 | 整数参数（XAR5 的低 16 位） |
| R0H | 32-bit 浮点 | float32 参数 |
| R1H | 32-bit 浮点 | float32 参数 |
| 栈 (SP) | 任意 | 当寄存器不够时，额外参数通过栈传递 |

参数顺序不固定，建议 AL、AH、XAR4、XAR5 的常见顺序作为初始猜测，但必须通过分析确认。

**参数识别的可靠方法**：分析被还原函数的入口处，找到那些在函数体内**直接使用但从未被赋值**的寄存器——这些就是从调用者传入的参数。具体做法：
1. 从函数入口开始扫描，如果某个寄存器在第一次被读取之前没有被写入，则它是入参。
2. 如果函数序言中有 `MOV *-SP[k], AL` 这样把寄存器存入栈的操作，说明该寄存器是参数，后续通过栈局部变量访问。
3. 结合调用点（caller）佐证：观察调用者在 `LCR` 前给哪些寄存器赋值。

示例 — InitSysPll_8683E 的参数识别：
```
; 调用者 InitSysCtrl_8BB1B 在调用前的赋值：
8bb3f  movb    AL,#1       ; → 参数 AL
8bb41  movb    AH,#20      ; → 参数 AH
8bb42  movb    XAR4,#0     ; → 参数 AR4
8bb40  movb    XAR5,#1     ; → 参数 AR5

; 被调函数 InitSysPll_8683E 入口处的使用：
86843  mov     *-SP[3],AL  ; AL 未赋值直接存栈 → 入参
86842  movz    AR7,@AH     ; AH 未赋值直接使用 → 入参
86852  movz    AR6,@AR4    ; AR4 未赋值直接使用 → 入参
86841  mov     *-SP[4],AR5 ; AR5 未赋值直接存栈 → 入参
```
最终签名: `void InitSysPll_8683E(Uint16 AL, Uint16 AH, Uint16 AR4, Uint16 AR5)`

**栈传参**：当参数超过寄存器容量时，额外参数通过栈传递。在 IDA 反汇编中表现为函数入口处通过 `*-SP[N+offset]` 访问的 arg_XX 变量（offset 位于栈帧分配范围之外）。

### 返回值识别

可返回值的寄存器：

| 寄存器 | 数据宽度 | C 类型 |
|--------|----------|--------|
| AL | 16-bit | Uint16 / int16 |
| ACC (AH:AL) | 32-bit | Uint32 / int32 |
| R0H | 32-bit 浮点 | float32 |

**返回值识别的可靠方法**：检查函数所有出口路径（`LRETR` 前），是否有对 AL、ACC 或 R0H 的赋值且该值未被后续指令消费。如果有，则该寄存器是返回值；如果没有，则函数是 void。

### 寄存器分类

定点寄存器：
- ACC (AH:AL) — 32-bit 累加器，用于定点运算、比较、地址计算
- XAR0 ~ XAR7 — 32-bit 辅助寄存器，XAR4/XAR5 常用于指针
- AR0 ~ AR7 — 对应 XAR 的低 16-bit，用于 16-bit 整数操作
- P (PH:PL) — 32-bit 乘积寄存器
- T — 16-bit 临时/移位量寄存器
- SP — 16-bit 栈指针

浮点寄存器 (FPU32)：
- R0H ~ R7H — 32-bit IEEE754 浮点寄存器
- 当汇编出现 R0H-R7H 以及 MPYF32/ADDF32/SUBF32/MOV32 等 FPU 指令时，相关数据是 float32 类型


### 参数识别

可传递参数的寄存器：

| 寄存器 | 数据宽度 | 典型用途 |
|--------|----------|----------|
| AL | 16-bit 定点 | 整数参数 (Uint16 / int16) |
| AH | 16-bit 定点 | 整数参数 |
| ACC (AH:AL) | 32-bit 定点 | 长整数参数 (Uint32 / int32) |
| XAR4 | 32-bit 地址 | 指针参数，或 32-bit 整数 |
| XAR5 | 32-bit 地址 | 指针参数，或 32-bit 整数 |
| AR4 | 16-bit 定点 | 整数参数（XAR4 的低 16 位） |
| AR5 | 16-bit 定点 | 整数参数（XAR5 的低 16 位） |
| R0H | 32-bit 浮点 | float32 参数 |
| R1H | 32-bit 浮点 | float32 参数 |
| 栈 (SP) | 任意 | 当寄存器不够时，额外参数通过栈传递 |

参数顺序不固定，建议 AL、AH、XAR4、XAR5 的常见顺序作为初始猜测，但必须通过分析确认。

**参数识别的可靠方法**：分析被还原函数的入口处，找到那些在函数体内**直接使用但从未被赋值**的寄存器——这些就是从调用者传入的参数。具体做法：
1. 从函数入口开始扫描，如果某个寄存器在第一次被读取之前没有被写入，则它是入参。
2. 如果函数序言中有 `MOV *-SP[k], AL` 这样把寄存器存入栈的操作，说明该寄存器是参数，后续通过栈局部变量访问。
3. 结合调用点（caller）佐证：观察调用者在 `LCR` 前给哪些寄存器赋值。

示例 — function_1 的参数识别：
```
; 调用者 function_1 在调用前的赋值：
8bb3f  movb    AL,#1       ; → 参数 AL
8bb41  movb    AH,#20      ; → 参数 AH
8bb42  movb    XAR4,#0     ; → 参数 AR4
8bb40  movb    XAR5,#1     ; → 参数 AR5

; 被调函数 function_1 入口处的使用：
86843  mov     *-SP[3],AL  ; AL 未赋值直接存栈 → 入参
86842  movz    AR7,@AH     ; AH 未赋值直接使用 → 入参
86852  movz    AR6,@AR4    ; AR4 未赋值直接使用 → 入参
86841  mov     *-SP[4],AR5 ; AR5 未赋值直接存栈 → 入参
```
最终签名: `void function_1(Uint16 AL, Uint16 AH, Uint16 AR4, Uint16 AR5)`

**栈传参**：当参数超过寄存器容量时，额外参数通过栈传递。在 IDA 反汇编中表现为函数入口处通过 `*-SP[N+offset]` 访问的 arg_XX 变量（offset 位于栈帧分配范围之外）。

### 返回值识别

可返回值的寄存器：

| 寄存器 | 数据宽度 | C 类型 |
|--------|----------|--------|
| AL | 16-bit | Uint16 / int16 |
| ACC (AH:AL) | 32-bit | Uint32 / int32 |
| R0H | 32-bit 浮点 | float32 |

**返回值识别的可靠方法**：检查函数所有出口路径（`LRETR` 前），是否有对 AL、ACC 或 R0H 的赋值且该值未被后续指令消费。如果有，则该寄存器是返回值；如果没有，则函数是 void。

### 寄存器分类

定点寄存器：
- ACC (AH:AL) — 32-bit 累加器，用于定点运算、比较、地址计算
- XAR0 ~ XAR7 — 32-bit 辅助寄存器，XAR4/XAR5 常用于指针
- AR0 ~ AR7 — 对应 XAR 的低 16-bit，用于 16-bit 整数操作
- P (PH:PL) — 32-bit 乘积寄存器
- T — 16-bit 临时/移位量寄存器
- SP — 16-bit 栈指针

浮点寄存器 (FPU32)：
- R0H ~ R7H — 32-bit IEEE754 浮点寄存器
- 当汇编出现 R0H-R7H 以及 MPYF32/ADDF32/SUBF32/MOV32 等 FPU 指令时，相关数据是 float32 类型

示例 — function_2 的栈局部数组：
```asm
89883  addb    SP,#14       ; 分配 14 words
89898  movz    AR4,@SP
89899  subb    XAR4,#13
8989b  mov     *+XAR4[AR0],#0  ; 通过基址+偏移循环访问 → int16 数组
```
推断: 栈上分配了一个 int16[14] 或类似结构的局部数组。

## 控制流识别

### 核心原则：分支方向反转

C28x 条件分支 `SB label, COND` 的语义是"条件成立则跳走"。因此：
- `SB label, EQ` 后面紧跟的代码是 **不相等** 时执行的（fall-through = 条件不成立）
- 跳转目标 label 处的代码是 **相等** 时执行的

还原 C 代码时，if 的条件是 fall-through 的条件（即分支条件的反面），除非你把 fall-through 写成 else。

条件码对照表：

| 条件码 | 汇编含义 | C 反面条件（fall-through） |
|--------|----------|---------------------------|
| EQ | == 时跳 | != |
| NEQ | != 时跳 | == |
| GT | 有符号 > 时跳 | <= |
| GEQ | >= 时跳 | < |
| LT | < 时跳 | >= |
| LEQ | <= 时跳 | > |
| HI | 无符号 > 时跳 | 无符号 <= |
| LOS | 无符号 <= 时跳 | 无符号 > |
| TC | 位测试为 1 时跳 | 位测试为 0 |
| NTC | 位测试为 0 时跳 | 位测试为 1 |


### if/else

基本模式：
```asm
CMP reg, #value
SB label, COND      ; 条件成立 → 跳到 label
; ... fall-through 代码（条件不成立时执行）...
SB end, UNC          ; 跳过 else
label:
; ... 条件成立时执行 ...
end:
```

如果没有 `SB end, UNC`（即 fall-through 后没有无条件跳转），说明没有 else 分支，只是一个单独的 if。

**零值判断的特殊形式**：C28x 部分指令（如 MOV、AND、SUB）会自动设置零标志位，无需显式 CMP：
```asm
MOV AL, *-SP[3]      ; 加载值，自动设置 Z 标志
SB label, EQ         ; 等于 0 则跳 → if(val != 0) { fall-through }
```

### 多条件 AND（连续跳转到同一目标）

当多条连续的条件分支都跳向**同一目标**时，表示多条件 AND：
```asm
CMP reg1, #val1
SB skip, NEQ         ; 第一个条件不等 → 跳过
CMP reg2, #val2
SB skip, NEQ         ; 第二个条件不等 → 跳过
CMP reg3, #val3
SB skip, NEQ         ; 第三个条件不等 → 跳过
; ... 三个条件都满足时执行的代码 ...
skip:
```
等价 C 代码：`if (reg1 == val1 && reg2 == val2 && reg3 == val3) { ... }`

识别要点：多条 SB 跳向**相同标签**，每条 SB 的条件都是"不满足则跳走"——只有全部不跳（全部条件成立）才进入下方代码块。

### 多条件 OR

与 AND 相反，多条分支跳向**同一个执行体**：
```asm
CMP reg1, #val1
SB body, EQ          ; 满足条件 1 → 进入 body
CMP reg2, #val2
SB body, EQ          ; 满足条件 2 → 进入 body
SB skip, UNC         ; 都不满足 → 跳过
body:
; ... 任一条件满足时执行 ...
skip:
```
等价 C 代码：`if (reg1 == val1 || reg2 == val2) { ... }`

### 链式 if-else-if

同一个变量依次与不同常量比较，每次比较后跳向不同的代码块：
```asm
CMPB AL, #0
SB block_0, EQ       ; val == 0 → 跳到 block_0
CMPB AL, #1
SB block_1, EQ       ; val == 1 → 跳到 block_1
CMPB AL, #2
B block_2, NEQ       ; val != 2 → 跳走（即 val == 2 fall-through）
; ... block_2 代码 ...
```
等价 C 代码：
```c
if (val == 0) { /* block_0 */ }
else if (val == 1) { /* block_1 */ }
else if (val == 2) { /* block_2 */ }
```

与 switch 的区别：链式 if-else-if 中各分支的代码块结尾通常跳向同一个出口，且分支目标代码量差异较大。如果各值连续且代码结构对称，可写成 switch。

### switch/case

识别标准——同时满足以下条件时还原为 switch：
1. 同一个寄存器被连续 CMPB/CMP 与多个**不同常量**比较
2. 每次比较后用 SB/B EQ 跳到各自独立的代码块
3. 最后有一个 SB/B UNC 跳到 default 或结尾
4. 各代码块结构相似（通常操作不同的外设实例或结构体实例）

通用模式：
```asm
MOV AL, *+XARn[offset]  ; 读取 switch 变量
CMPB AL, #const1
SB case1, EQ
CMPB AL, #const2
SB case2, EQ
CMPB AL, #const3
SB case3, EQ
SB default, UNC         ; 或 B end, UNC
```
等价 C 代码：
```c
switch (obj->field) {
    case const1: /* ... */ break;
    case const2: /* ... */ break;
    case const3: /* ... */ break;
    default: /* ... */ break;
}
```

注意：如果常量不连续（如 0, 5, 100）或各分支结构差异大，用 if-else-if 更准确。

### while / for 循环

**识别标准**：存在向上的回跳（跳回地址小于当前地址）。

典型模式——条件前置循环：
```asm
loop_top:
    CMP reg, #bound
    SB loop_end, COND    ; 退出条件满足时跳出
    ; ... 循环体 ...
    SB loop_top, UNC     ; 回跳
loop_end:
```

典型模式——条件后置循环（do-while）：
```asm
loop_top:
    ; ... 循环体 ...
    CMP reg, #bound
    SB loop_top, COND    ; 继续条件满足时回跳
```

循环变量通常通过 `ADDB reg, #1` / `SUBB reg, #1` / `INC` 递增递减。如果能明确计数器、初始值、终止条件，写成 for；否则写成 while。

### BANZ 循环

`BANZ label, ARn--` 是 C28x 专用的计数循环指令：ARn 非零则跳转并自减。
```asm
MOV AR6, #N
loop:
    ; ... 循环体 ...
    BANZ loop, AR6--     ; AR6 != 0 → AR6--, 跳回 loop
```
等价 C 代码：`for (int i = N; i >= 0; i--) { ... }`（注意 BANZ 执行 N+1 次）

### RPT 指令

`RPT #N` 重复执行紧跟的下一条指令 N+1 次。

- `RPT #N || NOP` → 纯延时，写为 `asm(" RPT #N || NOP");`
- `RPT #15 || SUBCU ACC, reg` → 16-bit 除法内建函数 `__rpt_subcu(acc, divisor, 15);`
- 其他 `RPT #N || 指令` → 根据被重复的指令语义还原，如 `RPT #N || PREAD` 用于块拷贝


## 结构体字段访问

通过指针 + 偏移访问结构体字段：

```asm
MOVB XAR0, #21          ; 偏移 21
MOV AH, *+XAR4[AR0]     ; AH = obj->字段_at_offset_21
```

偏移计算规则: Uint16/int16 占 1 word，float32/Uint32/int32 占 2 words。逐字段累加确定偏移。


## 外设寄存器访问

外设寄存器在汇编中通过 DP 寻址或指针间接寻址访问。IDA 不存储位域信息，还原位域赋值需要结合工程头文件计算。

### 还原步骤

**第一步：从汇编确定外设符号和偏移**

DP 寻址模式（最常见）：
```asm
MOVW DP, #_PeriphRegs+N     ; IDA 会标注外设符号名和 DP 页偏移
MOV @offset, AL              ; 或 OR @offset, #imm / AND @offset, #imm
```
- `MOVW DP, #_PeriphRegs+N` 中的 `_PeriphRegs` 是 IDA 已识别的外设结构体全局变量名（如 `_GpioDataRegs`、`_EPwm1Regs`、`_SpibRegs`）
- DP 页偏移 N 和指令中的 @offset 共同决定访问的是结构体内第几个 word

指针间接寻址（用于动态选择外设实例）：
```asm
MOVL XAR4, *-SP[k]          ; 加载外设基址指针
MOV *+XAR4[offset], AL      ; 写入偏移 offset 处
```

**第二步：从头文件定位具体寄存器和位域**

工程 `include/` 目录中的 `F2837xS_外设名.h` 头文件包含完整的结构体和位域定义。通过外设名（从 IDA 符号中提取）定位对应头文件。

头文件中的外设结构体采用统一的 union 模式：
```c
union XXX_REG {
    Uint16 all;     // 或 Uint32 all（取决于寄存器宽度）
    struct XXX_BITS bit;  // 位域定义
};
struct PERIPH_REGS {
    union REG1_REG  REG1;   // offset 0（1 word 或 2 words）
    union REG2_REG  REG2;   // offset 紧接上一个
    ...
};
```


从 DP 页偏移和 @offset 计算结构体内总偏移（单位: 16-bit word），然后在结构体定义中逐字段累加找到对应的 union 成员。注意 Uint16 占 1 word，Uint32 占 2 words。

**第三步：根据操作类型确定 .all 还是 .bit 访问**

- `MOV @offset, AL`（整 word 写入）→ `Regs.REG.all = value;`
- `OR @offset, #mask`（置位）→ `Regs.REG.bit.FIELD = 1;`（查头文件中 mask 对应的位域名）
- `AND @offset, #mask`（清位）→ `Regs.REG.bit.FIELD = 0;`（mask 的反码位对应的位域）
- `MOV AL, @offset` 后 `ANDB AL, #mask` → 读位域

位域对应关系：mask 值中为 1 的位位置，在头文件的 `struct XXX_BITS` 中从 bit0 开始逐字段累加位宽找到对应字段名。


## 浮点指令 (FPU32)

| 汇编 | C 等价 |
|------|--------|
| MPYF32 R0H, R1H, R2H | R0H = R1H * R2H |
| ADDF32 R0H, R1H, R2H | R0H = R1H + R2H |
| SUBF32 R0H, R1H, R2H | R0H = R1H - R2H |
| ABSF32 R0H, R1H | R0H = fabsf(R1H) |
| NEGF32 R0H, R1H | R0H = -R1H |
| F32TOI16 R0H, R1H | (int16)float_val |
| I16TOF32 R0H, mem | (float32)int_val |
| CMPF32 R0H, R1H | 浮点比较，检查后续分支条件 |

上述表格中只是部分浮点指令列举，当指令出现以 32 结尾时是浮点指令，操作的寄存器肯定有浮点寄存器。

浮点常量: `MOVIZ` + `MOVXI` 两条指令组合加载 IEEE754 立即数，分别是高16位和低16位，需解码为浮点值。


## 全局变量与常量

### 识别方式

IDA 中每个 Segment 有类型标记（CODE / DATA）。DATA 段中存放的地址即全局变量或常量。

在汇编中，全局变量通过两种方式访问：

**DP 寻址**（最常见）：
```asm
MOVW DP, #_symbolName+N     ; IDA 标注的符号名 + DP 页偏移
MOV @offset, AL              ; 写入：总偏移 = N * 64 + offset（单位: word）
MOV AL, @offset              ; 读取
```
`_symbolName` 是 IDA 在 DATA 段中已识别的全局符号。如果符号是结构体变量，总偏移对应结构体内的某个字段。

**绝对地址 / 指针间接**：
```asm
MOVL XAR4, #0x11140          ; 加载全局变量的绝对地址
MOV *+XAR4[offset], AL      ; 通过指针+偏移访问
```

### 类型推断

全局变量的类型由访问指令决定（与局部变量规则一致）：
- `MOV` 单 word 访问 → Uint16 / int16
- `MOVL` 双 word 访问 → Uint32 / int32 或指针
- `MOV32` 访问 → float32
- 如果 IDA 已为该地址标注了符号名（如 `_u16_varName_ADDR`），名称中的类型前缀即为类型

### 结构体类型的全局变量

当同一个符号被不同偏移反复访问时，说明它是结构体变量。字段定位方法：
1. 从 IDA 获取符号名和基址
2. 在工程头文件（如 `include/gvar.h`）中查找该结构体的定义
3. 按 word 偏移逐字段累加（Uint16 占 1 word，Uint32/float32 占 2 words），定位到具体字段

### 常量与只读数据

DATA 段中只被读取从不被写入的地址是常量。常见形式：
- 查找表（如 CRC 表）：连续的固定值数组，通过 `PREAD` 或索引访问
- 浮点常量：存放在 DATA 段的 IEEE754 值，通过 `MOV32 R0H, *addr` 加载
- 字符串常量：连续的 ASCII 字节

常量在 C 代码中用 `const` 修饰，或放入独立的常量源文件。


## 命名规范

- 函数: `保持与IDA一致`，如 `sub_8C9CC`
- 类型前缀: u16_ i16_ u32_ i32_ f32_
- 全局变量: `分析类型前缀`，如 `u16_14908`
- 局部变量: 映射明确时用寄存器名 (`int16 XAR6;`)，否则用语义名 (`Uint16 temp;`)
- IDA 中已命名的符号原样保留，不改变，变量原始名`unk_xxx`，函数原始名`sub_xxx`

## 类型

```c
typedef unsigned char  Uint8;
typedef unsigned short Uint16;
typedef short          int16;
typedef unsigned long  Uint32;
typedef long           int32;
typedef float          float32;
```
符号性未被证明时，默认无符号。只有 signed 比较/扩展/算术才使用有符号类型。

## 还原原则

### 第一性原则：语义等价

还原的唯一目标是产出与原始汇编**语义等价**的 C 函数。语义等价的定义：对于所有可能的输入，还原后的 C 代码与原始汇编产生**相同的副作用序列和相同的返回值**。

副作用序列包括：
- 内存写入（全局变量赋值、结构体字段写入）的地址、值和顺序
- 外设寄存器访问（volatile 读写）的地址、值和顺序
- 函数调用的目标、参数和顺序

语义等价 ≠ 语法相似。C 代码不需要逐条对应汇编指令，只要最终效果一致即可。编译器的寄存器分配、指令调度、常量折叠等优化会使汇编形态与 C 源码差异很大，这是正常的。

### 还原策略：自顶向下，逐层细化

1. **确认函数边界**：从 IDA 获取函数起止地址、栈帧大小
2. **识别参数和返回值**：分析入口处未赋值即使用的寄存器、出口处被赋值的寄存器
3. **恢复控制流骨架**：先识别所有分支和跳转目标，建立 if/else/switch/loop 结构
4. **填充表达式**：在控制流骨架内，逐基本块还原赋值、运算、函数调用
5. **验证覆盖率**：每条有副作用的汇编指令都应有对应 C 语句，无遗漏无多余

### 硬件副作用保留原则

嵌入式固件直接与硬件交互，以下副作用必须原样保留，不得优化、合并或重排：

- **volatile 外设读写**：外设寄存器可被硬件异步修改，读写顺序和次数都有意义。即使看似冗余的连续写入也可能是硬件时序要求
- **EALLOW / EDIS**：保护寄存器的解锁/加锁必须成对出现，包裹范围必须与汇编一致
- **延时指令**：`RPT #N || NOP` 是精确的周期延时，必须保留为内联汇编
- **特殊序列**：看门狗喂狗（写 0x55 再写 0xAA）、Flash 初始化等多步序列的顺序不可改变

### 证据驱动，不可编造

- 每个还原结论必须有汇编证据支撑。没有对应指令的逻辑不写入 C 代码
- 类型、字段名、外设映射必须来自 IDA 符号或工程头文件，不凭经验猜测
- 不确定的地方标注 `// TODO: verify`，不把推测写成事实
- IDA 的 decompile（F5）输出只作参考，它在 C28x 上的准确率有限，必须以汇编为最终依据

### 结构化优先，goto 兜底

优先使用 if/else/switch/while/for 表达控制流。只有当跳转目标无法用结构化语句安全表达时（如跳入循环中间、跨越多层嵌套的 break），才保留 goto 并注释说明原因。
"""

