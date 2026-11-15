# -*- coding: utf-8 -*-
"""
构建带标签的评测数据集 eval/testset.jsonl
标签体系来自 end_new.py SimpleAgent.run() 的真实路由分支 + rag_docs 真实内容。
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
QS = os.path.join(ROOT, "test_200_questions.json")
OUT = os.path.join(BASE, "testset.jsonl")
DOCS = os.path.join(ROOT, "rag_docs")

KB = ["tool_search_kb"]
KB_W = ["tool_search_kb", "tool_get_weather"]
KB_D = ["tool_search_kb", "tool_get_distance"]
KB_G = ["tool_search_kb", "tool_get_gps"]

# id -> (task_type, expected_route, expected_tools, reference_answer, reference_sources)
L = {}

L[1] = ("system_intro", "react_agent", KB,
 "该系统用于实时监测城市或郊野边坡的位移、入侵和气象数据。感知层由C8T6单片机、毫米波雷达、摄像头、温湿度传感器和北斗/GPS模块组成；通信层用ESP8266 AP模式传距离数据、串口传北斗定位；决策层在上位机运行YOLOv8入侵检测、大模型Agent智能决策和RAG知识库问答。系统纯本地部署、数据不公网传输，支持雨雾图像增强、滑动窗口加冷却时间减少误报，并可远程控制LED和蜂鸣器。",
 ["system_intro.txt"])
L[2] = ("system_intro", "react_agent", KB,
 "系统分三层：感知层为C8T6单片机加毫米波雷达、摄像头、温湿度传感器和北斗/GPS模块；通信层用ESP8266 AP模式（热点名ESP8266_AP，IP 192.168.4.1，端口8000）传输距离数据，北斗定位走串口；决策层在上位机运行YOLOv8入侵检测、基于大模型的Agent智能决策和RAG知识库问答，告警通过微信文件传输助手和QQ邮件（SMTP）发出。",
 ["system_intro.txt"])
L[3] = ("system_intro", "react_agent", KB,
 "是联网的，但属于纯本地部署。系统在上位机与硬件之间通过ESP8266 AP热点组网，数据不公网传输。通信层用ESP8266 AP模式（热点名ESP8266_AP，IP 192.168.4.1，端口8000）传输距离数据，北斗定位通过串口传输；告警则通过微信文件传输助手和QQ邮件（SMTP）发出。",
 ["system_intro.txt"])
L[4] = ("hardware_setup", "react_agent", KB,
 "需要按三层配置硬件。感知层：C8T6单片机、毫米波雷达、摄像头、温湿度传感器、北斗/GPS模块；通信层：ESP8266模块（工作在AP模式，热点名ESP8266_AP，IP 192.168.4.1，端口8000），北斗定位走串口；决策层需要一台上位机，配置2核CPU@2.8GHz起步、推荐4核及以上，并具备WiFi网卡用于连接设备热点。",
 ["system_intro.txt", "os_compatibility.txt"])
L[5] = ("os_compatibility", "react_agent", KB,
 "可以。项目基于Python加Tkinter开发，理论上兼容所有能运行Python 3.13的操作系统，包括Windows、Linux、macOS，实际测试与落地以Windows为主。列出的Windows版本为Win7、Win10、Win Server 2012/2012 R2/2016（64位），也支持Ubuntu、CentOS及麒麟系统。硬件要求2核CPU@2.8GHz，推荐4核及以上。",
 ["os_compatibility.txt"])
L[6] = ("system_capability", "react_agent", KB,
 "当前版本为单点监测，不能同时覆盖多个边坡点。系统通过一套毫米波雷达加摄像头监测一个坡面，可用于城市或郊野边坡的位移、入侵与气象监测。如果需要监测多个点位，可以通过部署多套系统来扩展。",
 ["alert_rules_and_examples.txt", "system_intro.txt"])
L[7] = ("system_capability", "react_agent", KB,
 "系统包含低功耗设计。当夜间无入侵且位移变化小于0.1mm/10min时，会自动关闭显示器，并把采样频率降至1Hz，功耗降低60%。供电方面可用太阳能板，案例中曾出现积雪覆盖太阳能板导致断电约8小时、雪停后自动恢复供电并补传数据的情况，说明系统具备断电恢复能力。",
 ["slope_cases.txt"])
L[8] = ("system_capability", "react_agent", KB,
 "系统支持太阳能供电，并有低功耗模式配合：夜间无入侵且位移变化小于0.1mm/10min时自动关闭显示器、采样频率降至1Hz，功耗降低60%。案例#42记录到太阳能板清洁后电池电量恢复、低功耗模式正常；案例#101记录到太阳能板被积雪覆盖导致断电约8小时，雪停后自动恢复供电、数据补传正常。",
 ["slope_cases.txt"])
L[9] = ("system_capability", "react_agent", KB,
 "有周期性报告机制。案例#53记录到例行周报生成，位移月变化量小于0.02m；案例#49记录到正常数据系统记录归档。#70也提到例行数据备份，历史案例库已扩充至70条。报告依托上位机记录的历史监测数据自动汇总。",
 ["slope_cases.txt"])
L[10] = ("maintenance", "react_agent", KB,
 "需要人工巡检配合。案例中有人工巡检记录，例如#19例行人工巡检设备工作正常、#60人工巡检边坡表面无裂缝监测墩稳固、#94人工巡检边坡无裂缝，巡检后系统继续自动监测。日常主要靠系统自动采集与告警，人工负责周期性现场核查与设备维护。",
 ["slope_cases.txt"])
L[11] = ("system_ui", "react_agent", KB,
 "首页主要显示三类数据：毫米波雷达给出的边坡表面距离（单位厘米，连续监测可得位移变化速率）、北斗/GPS定位坐标、以及温湿度等气象数据；视频区显示YOLOv8的入侵检测结果。系统把距离、位移速率、天气、雨量、入侵和结论按时间记录，例如“位移:0.56m，速率:+0.01mm/h，天气:晴，雨量:0mm，入侵:无，结论:正常”。",
 ["slope_cases.txt", "slope_monitoring_principles.txt"])
L[12] = ("system_ui", "react_agent", [],
 None, [])
L[13] = ("system_capability", "react_agent", KB,
 "文档中只记录了ESP8266固件升级。案例#41记录到ESP8266固件升级后连接更稳定、未出现断线；#67记录到系统版本更新、Agent支持工具调用并测试通过；#29记录到系统升级后Agent工具调用测试通过。关于远程OTA升级官方文档没有明确说明，固件一般需要现场重新烧录。",
 ["slope_cases.txt"])
L[14] = ("data_management", "react_agent", KB,
 "文档没有给出统一的数据保留期限。案例库本身记录的是2024-01-01至2026-06-01的运维数据，并按编号持续归档；系统会做例行数据备份，历史案例库已扩充至70条，之后又更新至58条案例的向量库。能否查到三个月前的数据取决于上位机本地存储情况，文档未作承诺。",
 ["slope_cases.txt"])
L[15] = ("data_management", "react_agent", [],
 None, [])
L[16] = ("positioning", "react_agent", KB_G,
 "靠北斗坐标标点。系统把北斗坐标用于在地图上标出边坡点位，辅助应急决策。感知层配有北斗/GPS模块，定位数据经串口传入上位机；软件侧已有slope_map.html用于点位展示。因此只要北斗模块正常输出坐标，就能在地图上定位该边坡监测点。",
 ["slope_monitoring_principles.txt", "system_intro.txt"])
L[17] = ("system_ui", "react_agent", KB,
 "摄像头画面在上位机界面查看。系统在上位机运行YOLOv8入侵检测，监测区域设定为(160,100,480,380)，检测到类别0（人）、15（猫）、16（狗）在该区域内即触发入侵告警；摄像头支持笔记本摄像头或USB摄像头，并在夜间使用红外补光。文档未提到手机端查看，系统为纯本地部署。",
 ["alert_rules_and_examples.txt", "slope_cases.txt"])
L[18] = ("system_capability", "react_agent", KB,
 "主要优势是纯本地部署、数据不公网传输。系统集成毫米波雷达测距、YOLOv8入侵检测与大模型Agent加RAG知识库问答；支持雨雾天气图像增强（convertScaleAbs或All-in-One Network）、短期滑动窗口加冷却时间减少误报，可远程通过TCP指令控制LED和蜂鸣器，告警经微信与QQ邮件送达。",
 ["system_intro.txt"])
L[19] = ("system_capability", "react_agent", KB,
 "扩展方向已有规划：向量检索RAG采用FAISS加sentence-transformers，按chunk_size=200、overlap=20切块后向量化；ReAct Agent用LangChain的initialize_agent，定义GetWeather和SearchHistory工具由Agent自主选择；上下文记忆用短期滑动窗口保留最近5轮、长期记忆存事件摘要。",
 ["agent_rag_design.txt"])
L[20] = ("project_background", "react_agent", [],
 None, [])
L[21] = ("installation", "react_agent", KB,
 "安装难度不大但需要按层接线调试。先按感知层接好C8T6单片机、毫米波雷达、摄像头、温湿度传感器和北斗/GPS模块，通信层配置ESP8266为AP模式（热点ESP8266_AP，IP 192.168.4.1，端口8000），北斗走串口；然后在上位机装好Python依赖并运行程序。文档也提示上位机兼容Windows、Linux、macOS，实际以Windows为主。",
 ["system_intro.txt", "os_compatibility.txt"])
L[22] = ("installation", "react_agent", KB,
 "要装在高处并避开遮挡。雷达观测方向内应避免树木等遮挡，因为植被会形成遮蔽角和监测盲区；案例#15记录了树叶遮挡雷达导致位移速率跳变误报，清理后数据才恢复。摄像头选用笔记本或USB摄像头并保证驱动正常，夜间靠红外补光。避免墙体遮挡以保证ESP8266信号，RSSI不低于-80dBm。",
 ["radar_vegetation_interference.txt", "hardware_troubleshooting.txt", "slope_cases.txt"])
L[23] = ("installation", "react_agent", KB,
 "安装要注意角度、遮挡和供电。雷达观测方向内不应有树木等遮挡物，否则会形成遮蔽角与监测盲区、造成误报；北斗天线必须置于室外空旷处、金属面朝天空；摄像头要避免逆光和抖动，保证夜间红外补光正常；ESP8266要避免墙体遮挡、独立供电，RSSI不低于-80dBm，并预留防水防潮措施。",
 ["radar_vegetation_interference.txt", "hardware_troubleshooting.txt"])
L[24] = ("installation", "react_agent", KB,
 "会有明显影响。雷达阵地周边的高大茂密树木会对主要观测方向形成大范围遮蔽角，地物回波图出现大片监测盲区，雷达有效探测半径缩减；短波长SAR数据植被穿透能力弱，易严重失相干，导致滑坡目标漏判误判频发。案例#15就是树叶遮挡雷达造成数据跳变误报，清理后恢复正常。",
 ["radar_vegetation_interference.txt", "slope_cases.txt"])
L[25] = ("hardware_setup", "react_agent", KB,
 "两类连接。距离数据走网络：ESP8266工作在AP模式，热点名ESP8266_AP，IP 192.168.4.1，端口8000，上位机连上该热点后通过TCP读取距离；北斗定位走串口，接线为模块TXD接转换器RXD，默认波特率9600（程序中为115200），需两端一致。硬件TCP端口还需与C8T6代码一致。",
 ["system_intro.txt", "hardware_troubleshooting.txt"])
L[26] = ("hardware_setup", "react_agent", KB,
 "按距离数据通道接入。确认C8T6代码中Wi-Fi模式为AP、TCP端口为8080（系统简介中为192.168.4.1:8000），用网络调试助手连接后发送GET_DIST\\n即可读取雷达距离；同时检查Serial串口是否输出原始距离值。上位机侧由TCP线程接收，掉线后会每隔3秒尝试重连。",
 ["hardware_troubleshooting.txt", "system_intro.txt"])
L[27] = ("hardware_setup", "react_agent", KB,
 "文档没有给出唯一推荐值。案例中出现过因摄像头处理占用资源导致卡顿的情况，建议在卡顿时降低图像分辨率或帧率，例如从640x480降到320x240。摄像头分辨率过高或过低都会破坏YOLOv8的检测精度，因此需在清晰度与算力之间折中，用笔记本或USB摄像头并保证驱动正常。",
 ["hardware_troubleshooting.txt", "yolo_troubleshooting.txt"])
L[28] = ("hardware_setup", "react_agent", KB,
 "要分场景设置。北斗模块默认波特率为9600，必须与串口助手设置一致；而上位机程序中北斗串口默认按115200接收，因此需让模块与程序两端保持一致。若出现乱码，首先检查波特率是否匹配，其次检查串口线是否接触不良、过长或受干扰。",
 ["hardware_troubleshooting.txt"])
L[29] = ("alert_rules", "react_agent", KB,
 "阈值固化在代码里。告警规则是距离小于0.3米触发边坡距离异常告警，该阈值写在代码中的ALERT_DISTANCE变量上，修改后需要重新编译。当前版本不支持用户自定义阈值，未来版本计划开放配置界面。",
 ["alert_rules_and_examples.txt"])
L[30] = ("alert_rules", "react_agent", KB,
 "冷却时间是为了避免告警轰炸，默认10秒。同一类型告警在冷却期内不会重复发送；系统还支持滑动窗口，连续3次异常才触发告警，以过滤瞬时抖动。当前版本冷却时间不支持用户自行修改，如需调整要改代码。",
 ["alert_rules_and_examples.txt"])
L[31] = ("data_management", "react_agent", [],
 None, [])
L[32] = ("maintenance", "react_agent", [],
 None, [])
L[33] = ("maintenance", "react_agent", KB,
 "可以自动实现。系统的低功耗模式规定：夜间无入侵且位移变化小于0.1mm/10min时，自动关闭显示器，采样频率降至1Hz，功耗降低60%。该逻辑由程序自动判断执行，不需要单独设置开关；案例#77也记录了太阳能供电正常、电池电压稳定的运行状态。",
 ["slope_cases.txt"])
L[34] = ("data_management", "event_query", KB,
 "日志可在上位机程序中查看。系统会在界面输出运行日志，例如“WiFi连接断开，尝试重连...”提示；案例#142记录的是ESP8266断线重连的日志记录。系统同时把每次监测结果按时间归档，形如“2026-04-30 08:00:00 位移:0.53m，速率:+0.09mm/h，天气:中雨，结论:红色预警”。",
 ["hardware_troubleshooting.txt", "slope_cases.txt"])
L[35] = ("documentation", "react_agent", [],
 None, [])
L[36] = ("installation", "react_agent", KB,
 "建议按层上电并逐级验证。先接好感知层的C8T6单片机、毫米波雷达、摄像头、温湿度传感器和北斗/GPS模块；再配置ESP8266为AP模式（热点ESP8266_AP，IP 192.168.4.1，端口8000）并确认串口有原始距离值；然后在上位机装好Python依赖启动程序，用网络调试助手发送GET_DIST\\n验证距离数据是否正常。",
 ["system_intro.txt", "hardware_troubleshooting.txt"])
L[37] = ("installation", "react_agent", KB,
 "需要确认摄像头和北斗相关驱动。摄像头要保证正确驱动（笔记本摄像头或USB摄像头），可在设备管理器中检查是否有未知设备；北斗/GPS模块通过USB转TTL接入，若电压正常仍无数据，可换一个USB转TTL测试；使用Ollama本地模型时还可能涉及模型目录权限与端口配置。",
 ["hardware_troubleshooting.txt", "ollama_troubleshooting.txt"])
L[38] = ("installation", "react_agent", KB,
 "项目基于Python加Tkinter框架开发，理论上兼容所有可运行Python 3.13版本的操作系统，包含Windows、Linux、macOS，实际测试与落地以Windows系统为主。硬件基础要求为2核CPU@2.8GHz，推荐4核及以上。因此建议按Python 3.13配置环境，并在Windows上部署。",
 ["os_compatibility.txt"])
L[39] = ("hardware_setup", "react_agent", KB_D,
 "用数据判断。雷达按FMCW原理连续测得边坡表面至雷达的距离（单位厘米），正常工作时上位机会持续刷新距离读数并能算出位移速率。若怀疑异常，可用网络调试助手连接AP后发送GET_DIST\\n读取距离，或检查Serial串口是否输出原始距离值；若读数恒为-1.0，说明未检测到有效回波。",
 ["slope_monitoring_principles.txt", "hardware_troubleshooting.txt"])
L[40] = ("installation", "react_agent", KB,
 "需要校准和验证。安装后应确认雷达安装姿态与观测方向无遮挡，避免树木形成遮蔽角；北斗天线须置于室外空旷处、金属面朝天空，并用万用表测TX空闲电压应不低于2.8V、TX与GND间电压在1.4V至3.3V间有跳变；再用网络调试助手发送GET_DIST\\n核验距离读数是否符合实际。",
 ["hardware_troubleshooting.txt", "radar_vegetation_interference.txt"])
L[41] = ("hardware_troubleshoot", "react_agent", KB,
 "先排查热点本身。检查电脑WiFi列表中是否存在ESP8266_AP，默认密码12345678；若看不到热点，重启ESP8266模块或重新烧录AP模式固件。同时注意ESP8266要避免墙体遮挡、建议独立电源供电，RSSI不低于-80dBm，信道干扰严重时用WiFi分析工具切换到空闲信道。",
 ["hardware_troubleshooting.txt"])
L[42] = ("hardware_troubleshoot", "react_agent", KB,
 "这是典型的“连上但收不到数据”。处理步骤：确认C8T6代码中Wi-Fi模式为AP，且TCP端口为8080；用网络调试助手尝试连接192.168.4.1:8080并发送GET_DIST\\n；检查Serial串口是否输出原始距离值。读数一直是-1.0表示雷达未检测到有效回波，可清理雷达前方遮挡物、重启模块并检查串口与网络连接。",
 ["hardware_troubleshooting.txt"])
L[43] = ("hardware_troubleshoot", "react_agent", KB,
 "数据时有时无通常是通信不稳。排查ESP8266：确保RSSI不低于-80dBm并避免墙体遮挡，用WiFi分析工具检查信道干扰并切换到空闲信道，改用独立电源供电而非USB供电，升级到最新稳定版SDK或AT固件v2.2.1以上，并在代码中加入自动重连机制和事件回调监听。案例#17就是断线重连、数据中断5分钟后自动恢复。",
 ["hardware_troubleshooting.txt", "slope_cases.txt"])
L[44] = ("hardware_troubleshoot", "react_agent", KB,
 "不一定坏，-1.0表示传感器未检测到有效回波。常见原因有三：雷达前方有障碍物或传感器被遮挡；测量距离超出有效量程（例如超过2米）；模块未正确初始化或通信协议错乱。建议清理雷达前方区域、重启模块，并检查串口/网络连接后复测。",
 ["hardware_troubleshooting.txt"])
L[45] = ("hardware_troubleshoot", "react_agent", KB,
 "逐项排查。检查USB接口是否松动或供电不足，可换一个USB口或使用带供电的USB HUB；确认摄像头驱动是否正确安装；关闭其他占用摄像头的程序；降低图像分辨率或帧率，例如从640x480降到320x240；并检查上位机CPU负载是否过高。",
 ["hardware_troubleshooting.txt"])
L[46] = ("hardware_troubleshoot", "react_agent", KB,
 "画面模糊多与图像质量和分辨率有关。YOLOv8漏检原因中包含画面模糊、拍摄抖动、对比度过低，以及分辨率过高或过低破坏检测精度。可调整图像分辨率避免过度压缩失真，开启图像增强提高对比度：常规做法是convertScaleAbs(alpha=1.4, beta=20)，雨雾天气可用All-in-One Network去雨去雾雪。",
 ["yolo_troubleshooting.txt", "slope_monitoring_principles.txt"])
L[47] = ("hardware_troubleshoot", "react_agent", KB,
 "按六步排查北斗串口无数据：供电检查，确认模块电源灯亮并测TX空闲电压应不低于2.8V；接线检查，TXD应接转换器RXD，接反则无数据，可对调TXD/RXD测试；波特率确认，模块默认9600需与串口助手一致；天线与环境必须室外空旷、金属面朝天空；信号测试TX与GND间电压应在1.4V至3.3V间有跳变；仍无数据时换一个USB转TTL测试。",
 ["hardware_troubleshooting.txt"])
L[48] = ("hardware_troubleshoot", "react_agent", KB,
 "乱码多因参数或线路问题。检查波特率是否匹配，应与程序中一致（默认115200）；检查串口连接线是否接触不良、过长或受干扰；确认接收数据格式是否为NMEA 0183协议，可换用串口助手测试。如果乱码偶发，可增加数据校验或重启串口线程。",
 ["hardware_troubleshooting.txt"])
L[49] = ("hardware_troubleshoot", "react_agent", KB,
 "从信号、信道、供电、固件和代码五方面入手：确保RSSI不低于-80dBm并避免墙体遮挡；用WiFi分析工具检查信道干扰并切换至空闲信道；建议用独立电源而非USB供电；升级至最新稳定版SDK或AT固件v2.2.1以上；在代码中加入自动重连机制和事件回调监听。案例#41显示固件升级后连接更稳定、未再断线。",
 ["hardware_troubleshooting.txt", "slope_cases.txt"])
L[50] = ("hardware_troubleshoot", "react_agent", KB,
 "会自动重连。TCP线程检测到连接异常后，每隔3秒尝试重新连接服务器（IP:192.168.0.48:8080），直至成功，日志中会输出“WiFi连接断开，尝试重连...”提示。案例#17也记录到ESP8266断线重连、数据中断5分钟后自动恢复。",
 ["hardware_troubleshooting.txt", "slope_cases.txt"])
L[51] = ("hardware_troubleshoot", "react_agent", KB,
 "按微信通道排查。程序启动后会自动弹出微信登录二维码，需要扫码登录；检查itchat库是否正常安装；若仍失败可重启程序重新登录。另外可先用案例#189的思路确认微信告警通道本身是否正常，再检查网络与登录状态。",
 ["hardware_troubleshooting.txt"])
L[52] = ("hardware_troubleshoot", "react_agent", KB,
 "太阳能供电要配合低功耗模式。系统在夜间无入侵且位移变化小于0.1mm/10min时，自动关闭显示器、采样频率降至1Hz，功耗降低60%，可显著降低耗电。案例#42记录到太阳能板清洁后电池电量恢复、系统低功耗模式正常；案例#101则记录积雪覆盖太阳能板导致断电约8小时，雪停后自动恢复供电并补传数据。",
 ["slope_cases.txt"])
L[53] = ("hardware_troubleshoot", "react_agent", KB,
 "基本可用但需调参。案例#20记录小雪天气下低温导致雷达噪声增加，已调整滤波参数；案例#101记录大雪覆盖太阳能板导致断电约8小时，雪停后自动恢复供电、数据补传正常。因此低温会影响雷达噪声和供电，需要相应调整滤波并做好电池与太阳能板的维护。",
 ["slope_cases.txt"])
L[54] = ("hardware_troubleshoot", "react_agent", KB,
 "能做但精度会下降。雨雾天气下YOLO准确率会下降，案例#18记录雾天准确率降至70%，启用图像增强后恢复至85%。系统支持雨雾天气图像增强，可用convertScaleAbs提高对比度或All-in-One Network同时去雨、雾、雪。位移监测方面，降雨会使位移速率与雨量、降雨时长正相关，需加强关注。",
 ["optimization_cases.txt", "slope_monitoring_principles.txt"])
L[55] = ("hardware_setup", "react_agent", KB,
 "通过上位机远程控制面板或TCP指令。上位机界面右侧设有远程控制面板，可分别控制3个LED和蜂鸣器；也支持通过TCP指令远程控制，例如发送“CMD_LED_1_1_END”打开LED1。",
 ["alert_rules_and_examples.txt"])
L[56] = ("hardware_troubleshoot", "react_agent", KB,
 "先核对指令与链路。确认指令格式正确，例如打开LED1应发送“CMD_LED_1_1_END”；确认上位机已连上ESP8266热点（192.168.4.1），TCP链路正常且未被冷却或重连中断；再检查C8T6是否正常接收并执行指令、LED与蜂鸣器供电是否正常。",
 ["alert_rules_and_examples.txt", "hardware_troubleshooting.txt"])
L[57] = ("hardware_troubleshoot", "react_agent", KB,
 "设备发烫要分情况看。文档中提到的发热相关问题是CPU负载过高：摄像头处理可能消耗大量资源，应检查上位机或树莓派CPU负载，并降低分辨率或帧率（例如从640x480降到320x240）、关闭其他占用资源的程序。若模块本身异常发烫，建议检查供电是否稳定并加装散热或改用独立电源。",
 ["hardware_troubleshooting.txt"])
L[58] = ("hardware_troubleshoot", "react_agent", KB,
 "从占用资源的环节下手。检查是否有其他程序占用摄像头；降低图像分辨率或帧率，例如从640x480降到320x240；关闭其他占用CPU的程序；Ollama侧可用nvidia-smi和top查看GPU、CPU占用，资源紧张时改用更小的模型（如qwen2.5:0.5b）或做量化并流式输出。",
 ["hardware_troubleshooting.txt", "ollama_troubleshooting.txt", "agent_rag_design.txt"])
L[59] = ("hardware_setup", "react_agent", KB,
 "不宜过长。北斗/GPS串口出现乱码的原因之一就是串口连接线接触不良、过长或受干扰，因此应尽量使用短而屏蔽良好的线缆并检查接触是否牢固。另外接线时注意TXD要接转换器的RXD，接反则无数据，可对调测试。",
 ["hardware_troubleshooting.txt"])
L[60] = ("hardware_troubleshoot", "react_agent", KB,
 "换口或外供电。摄像头卡顿或黑屏的常见原因就是USB接口松动或供电不足，可尝试换一个USB口或使用带供电的USB HUB；同时确认摄像头驱动正确安装，关闭其他占用摄像头的程序，必要时降低分辨率与帧率。ESP8266也建议用独立电源而非USB供电。",
 ["hardware_troubleshooting.txt"])
L[61] = ("hardware_troubleshoot", "react_agent", KB,
 "优先查供电和接线。北斗/GPS模块排查要求先确认模块电源灯亮起，并用万用表测量TX空闲电压应不低于2.8V；ESP8266供电不稳也会导致工作异常，建议使用独立电源而非USB供电。若指示灯仍不亮，需检查电源适配器、线缆接触与模块是否损坏。",
 ["hardware_troubleshooting.txt"])
L[62] = ("hardware_troubleshoot", "react_agent", KB,
 "可能的原因包括：雷达前方有障碍物或传感器被遮挡；测量距离超出有效量程（例如超过2米）；模块未正确初始化或通信协议错乱。建议清理雷达前方区域、重启模块，并检查串口/网络连接；也可用网络调试助手连接192.168.4.1:8080发送GET_DIST\\n验证。",
 ["hardware_troubleshooting.txt"])
L[63] = ("hardware_troubleshoot", "react_agent", KB,
 "文档没有给出兼容性承诺。系统与雷达的对接依赖TCP端口与数据协议：需确认C8T6代码中Wi-Fi模式为AP、TCP端口为8080，并用GET_DIST\\n读取距离，Serial串口输出原始距离值。更换雷达后需要同步修改驱动与解析逻辑，建议先按同一协议做联调测试。",
 ["hardware_troubleshooting.txt"])
L[64] = ("hardware_troubleshoot", "react_agent", KB,
 "看三点：一是网络调试助手连接192.168.4.1:8080发送GET_DIST\\n是否能返回距离；二是Serial串口是否输出原始距离值；三是读数是否长期为-1.0。若持续为-1.0，可能是模块未正确初始化或通信协议错乱，也可能被遮挡或超出量程，可清障、重启模块后再验证。",
 ["hardware_troubleshooting.txt"])
L[65] = ("hardware_troubleshoot", "react_agent", KB,
 "文档没有专门的进水受潮处理章节，但可参考北斗/GPS串口无数据的排查思路：先断电检查接线与供电，确认TX空闲电压不低于2.8V，线缆接触良好且不过长。设备应做好室外防水防潮与独立供电，受潮后先彻底干燥再上电，避免短路损坏模块。",
 ["hardware_troubleshooting.txt"])
L[66] = ("alert_rules", "react_agent", KB,
 "距离小于0.3米（30厘米）以下触发边坡距离告警。该阈值固化在代码中的ALERT_DISTANCE变量上，当前版本不支持用户自定义，如需调整要修改代码后重新编译，未来版本计划开放配置界面。",
 ["alert_rules_and_examples.txt"])
L[67] = ("alert_rules", "react_agent", KB,
 "两种情况会触发。一是距离小于0.3米触发边坡距离异常告警；二是YOLO检测到类别0（人）、15（猫）、16（狗）且位于监测区域(160,100,480,380)内时触发入侵告警。告警冷却时间为10秒，避免重复轰炸。",
 ["alert_rules_and_examples.txt"])
L[68] = ("alert_rules", "react_agent", KB,
 "会触发，因为猫（类别15）和狗（类别16）都在入侵告警类别里。避免办法：调整监测区域，把常出没路径排除在(160,100,480,380)之外；适当提高置信度阈值；对夜间动物触发可人工确认后忽略，案例#21、#43、#68都是动物触发后确认无威胁并记录。",
 ["alert_rules_and_examples.txt", "slope_cases.txt"])
L[69] = ("alert_rules", "react_agent", KB,
 "通过微信和QQ邮件两种方式，微信可用。告警方式为微信（文件传输助手）和QQ邮件（SMTP）。微信告警需要程序启动后扫码登录，并确保itchat库正常安装；若扫码后仍发不出去，可重启程序重新登录，或检查itchat库与网络状态。",
 ["system_intro.txt", "hardware_troubleshooting.txt"])
L[70] = ("alert_rules", "react_agent", KB,
 "不会一直发。系统内置告警冷却时间，默认10秒，同一类型告警在冷却期内不会重复发送，因此不会轰炸你；还可结合滑动窗口机制，连续3次异常才触发告警，以减少瞬时抖动导致的频繁告警。",
 ["alert_rules_and_examples.txt"])
L[71] = ("alert_rules", "react_agent", KB,
 "能实现。系统支持滑动窗口，配置为连续3次异常才触发告警，可有效减少瞬时抖动导致的频繁告警。它与10秒告警冷却时间配合使用，前者过滤抖动、后者防止同一类型告警重复轰炸。",
 ["alert_rules_and_examples.txt"])
L[72] = ("alert_rules", "react_agent", KB,
 "最短间隔由告警冷却时间决定，默认10秒。同一类型告警在冷却期内不会重复发送，避免告警轰炸；若再配合滑动窗口机制（连续3次异常才触发告警），实际告警间隔会进一步拉长，因此不会出现密集轰炸。",
 ["alert_rules_and_examples.txt"])
L[73] = ("principle_knowledge", "react_agent", KB,
 "滑动窗口滤波是数据融合策略的一种：取最近5次距离数据求中位数作为当前值，用来抑制单点跳变与瞬时噪声。它的作用是减少误报，例如数据融合时当距离下降且监测区域内同时有人或车，才会触发更高级别的告警。",
 ["slope_monitoring_principles.txt"])
L[74] = ("principle_knowledge", "react_agent", KB,
 "按位移判据看：平均位移在10秒内变化小于2mm属正常；2至5mm为轻微滑动（黄色预警，满足任意2条即判定）；超过5mm则属严重滑动（危险级），《水电工程岸坡安全智能预警系统技术规程》将短期速率大于5mm/10s判定为失稳前兆。",
 ["displacement_criteria.txt"])
L[75] = ("principle_knowledge", "react_agent", KB,
 "会有影响，需做优化。树叶、飞虫遮挡雷达会造成误报，应清理现场并增加滑动窗口滤波；雨雾天气YOLO准确率下降，案例#18中准确率降至70%，启用图像增强后恢复到85%；通信瞬时中断也可通过自动重连和告警前确认连续多次异常来规避。",
 ["alert_rules_and_examples.txt", "optimization_cases.txt"])
L[76] = ("alert_rules", "react_agent", KB,
 "趋势预警是尚未达到阈值时的提前提示。案例记录的格式为：过去若干秒内距离从某值降至某值、给出降幅与速率，注明虽未触发0.3m阈值但下降趋势明显，建议加强监测；而告警是距离小于0.3米或检测到入侵等达到固定阈值时的正式触发。",
 ["cases.txt", "alert_rules_and_examples.txt"])
L[77] = ("alert_rules", "react_agent", KB,
 "按黄色预警（三级）对待：设置警示牌、加强日常巡检。可以查看位移速率与降雨情况，关注排水设施，必要时加密监测频次；系统提示的意思是当前虽未突破0.3米阈值，但位移下降趋势明显，需要人工确认坡面与设备状态。",
 ["early_warning_standards.txt", "cases.txt"])
L[78] = ("alert_rules", "react_agent", KB,
 "四级预警含义与处置：红色（单日位移≥20mm或连续3天速率≥2mm/d）需紧急撤离人员、全面交通管制；橙色（单日10～20mm或连续3天1～2mm/d）需疏散高风险区域人员、加密监测；黄色（单日5～10mm或累计位移超设计允许值80%）需设警示牌、加强巡检；蓝色（单日≤5mm）执行常规巡检。",
 ["early_warning_standards.txt"])
L[79] = ("alert_rules", "react_agent", KB,
 "满足任一即触发：单日位移不小于20mm，或连续3天位移速率不小于2mm/d，对应红色（一级）预警；位移判据中若超过15%监测点同时超限，或平均位移10秒内变化大于5mm，或出现大片连通滑动区域、位移曲线斜率突变，也属严重滑动报警。",
 ["early_warning_standards.txt", "displacement_criteria.txt"])
L[80] = ("alert_rules", "react_agent", KB,
 "依据位移量与速率分级。红橙黄蓝四级分别对应单日位移≥20mm或连续3天速率≥2mm/d、单日10～20mm或1～2mm/d、单日5～10mm或累计位移超设计允许值80%、单日≤5mm；雷达判据则参考95%监测点合格率、10秒变化2mm与5mm等指标。",
 ["early_warning_standards.txt", "displacement_criteria.txt"])
L[81] = ("alert_rules", "react_agent", KB,
 "可以同时触发。数据融合策略规定：当距离下降的同时监测区域内有人或车，就触发高级别告警。案例中多次同时出现位移异常与入侵记录，例如#37位移加速红色预警后#38继续增大，说明位移与入侵两类事件可以并存并分别记录。",
 ["slope_monitoring_principles.txt", "slope_cases.txt"])
L[82] = ("alert_rules", "react_agent", KB,
 "先定位原因再优化：树叶、飞虫遮挡雷达就清理现场并增加滑动窗口滤波；YOLO误检则调整监测区域或降低置信度阈值；通信瞬时中断要增加自动重连机制、告警前确认连续多次异常。案例#15就是树叶遮挡导致速率跳变误报，清理后数据恢复。",
 ["alert_rules_and_examples.txt", "slope_cases.txt"])
L[83] = ("alert_rules", "react_agent", KB,
 "当前不能。告警阈值固化在代码中，例如0.3米写在ALERT_DISTANCE变量里，用户不能自定义新规则；冷却时间和滑动窗口等参数也由系统内置。未来版本计划开放配置界面，届时才支持用户调整。",
 ["alert_rules_and_examples.txt"])
L[84] = ("alert_rules", "react_agent", KB,
 "目前必须改代码，不支持远程修改。阈值（如0.3米）固化在代码中，如需调整可修改代码中的ALERT_DISTANCE变量后重新编译；未来版本计划开放配置界面。上位机远程控制面板目前只用于控制LED和蜂鸣器。",
 ["alert_rules_and_examples.txt"])
L[85] = ("alert_rules", "react_agent", KB,
 "会执行对应等级的响应。红色预警需紧急撤离人员、全面交通管制；橙色需疏散高风险区域人员并加密监测；黄色需设警示牌、加强日常巡检；蓝色执行常规巡检。另外当距离下降且监测区域内有人或车时，会触发高级别告警。",
 ["early_warning_standards.txt", "slope_monitoring_principles.txt"])
L[86] = ("vision_yolo", "react_agent", KB,
 "YOLO是目标检测模型，在本系统中负责入侵检测。上位机运行YOLOv8，当检测到类别0（人）、15（猫）、16（狗）且位于监测区域(160,100,480,380)内时触发入侵告警，与毫米波雷达的位移监测一起构成边坡安全防线。",
 ["alert_rules_and_examples.txt"])
L[87] = ("vision_yolo", "react_agent", KB,
 "漏检原因有五类：小目标像素小于32x32时浅层特征易丢失；画面模糊、抖动、对比度过低；分辨率适配不当；置信度阈值偏高，默认0.45易滤掉真实目标；基础版YOLOv8n在复杂场景精度不足。对策：把CONF_THRESH从0.45调至0.2-0.3、改用YOLOv8-P2、校验模型文件、排查摄像头、用--save-txt输出日志。",
 ["yolo_troubleshooting.txt"])
L[88] = ("vision_yolo", "react_agent", KB,
 "用图像增强加参数调整。案例#18记录雾天准确率降至70%，启用图像增强（convertScaleAbs加All-in-One Network）后恢复到85%。具体做法：图像预处理增加All-in-One Network去雨去雾；把置信度阈值CONF_THRESH从0.45降到0.35；并增加连续帧检测，连续3帧检测到目标才告警以减少误报。",
 ["optimization_cases.txt"])
L[89] = ("vision_yolo", "react_agent", KB,
 "图像增强用于提高恶劣天气下的检测质量。常规增强是convertScaleAbs(alpha=1.4, beta=20)提高对比度；深度增强用All-in-One Network同时去雨、雾、雪，提升YOLO准确率。案例#18中雾天准确率降至70%，启用后恢复到85%。是否需要手动开启文档未明确，但会随天气影响自动或按需启用。",
 ["slope_monitoring_principles.txt", "optimization_cases.txt", "system_intro.txt"])
L[90] = ("agent_about_rag", "react_agent", KB,
 "指系统里的AI Agent（智能体）。它基于大模型，负责上位机的智能决策与RAG知识库问答，可自主决定调用工具；工具调用失败时会记录错误日志并降级回答，例如直接用知识库检索结果或返回友好提示，不会导致系统崩溃。",
 ["agent_rag_design.txt", "agent_troubleshooting.txt"])
L[91] = ("agent_about_rag", "react_agent", KB,
 "几乎覆盖系统相关知识，包括运行机制、设备自检、故障排查和数据统计等，并能调用工具获取天气、距离、定位等实时信息。升级方向包括向量检索RAG和ReAct Agent，由Agent自主决定调用哪个工具，并结合短期与长期记忆回答。",
 ["agent_rag_design.txt", "cases.txt"])
L[92] = ("agent_about_rag", "react_agent", KB,
 "相对可靠但并非万能。系统有反幻觉机制，工具调用失败时会记录错误日志并降级回答，例如直接用知识库检索结果或返回友好提示，不会让系统崩溃。检索为空时会暴露问题，系统通过增加同义词词典来优化，因此答案质量取决于知识库覆盖度。",
 ["agent_troubleshooting.txt"])
L[93] = ("agent_about_rag", "react_agent", KB_W,
 "可以。系统自己会真实调用天气查询工具获取天气信息，例如案例#44记录Agent调用天气API获知未来两天有小到中雨并建议关注；#62记录Agent调用天气API得知未来24小时仍有雨，把预警提升至黄色。",
 ["slope_cases.txt"])
L[94] = ("agent_about_rag", "react_agent", KB,
 "可以。Agent的设计中包含SearchHistory工具用于检索历史滑坡案例，系统也会自动检索相似案例，例如案例#34中Agent主动检索历史案例#4、#11并认为风险较低，#63检索到案例#5、#24、#46、#55认为位移趋势相似。",
 ["agent_rag_design.txt", "slope_cases.txt"])
L[95] = ("agent_about_rag", "react_agent", KB,
 "有记忆功能。系统采用上下文记忆机制：短期滑动窗口保留最近5轮对话，长期记忆存储重要事件摘要，避免token爆炸。因此它能记住你之前问过的内容，并在回答时结合最近几轮上下文，而不是每次从零开始。",
 ["agent_rag_design.txt"])
L[96] = ("agent_about_rag", "react_agent", KB,
 "RAG是检索增强生成：先从本地知识库检索相关内容，再让模型基于检索结果回答。系统当前已实现基于关键词匹配的问答（SimpleRAG），知识库为txt文档；下一步将升级为向量检索RAG，用FAISS加sentence-transformers(all-MiniLM-L6-v2)，按chunk_size=200、overlap=20切块后向量化，用户问句先语义检索再生成。",
 ["agent_rag_design.txt"])
L[97] = ("agent_about_rag", "react_agent", KB,
 "知识库是本地txt文档集合，包含系统简介、硬件与故障排查、告警规则、预警标准与位移判据、滑坡案例、YOLO与Ollama排查等内容，并用FAISS做向量化存储。案例库会持续更新，例如已扩充至70条，向量库也更新至58条案例。",
 ["agent_rag_design.txt", "slope_cases.txt"])
L[98] = ("agent_about_rag", "react_agent", KB,
 "文档没有说明自助上传入口。当前知识库为本地txt文档，检索靠关键词匹配，并已引入FAISS向量库、随案例积累更新；要新增自有文档，需要把文档放入知识库目录并重建索引，属于运维操作而非界面功能。",
 ["agent_rag_design.txt", "slope_cases.txt"])
L[99] = ("agent_about_rag", "react_agent", KB,
 "向量检索指先把知识库文档切块（chunk_size=200、overlap=20）再向量化存入FAISS，用户问句也向量化后在向量空间中做语义检索，命中的片段交给大模型生成回答。相比纯关键词匹配，它能召回语义相近但用词不同的内容，减少“关键词未命中”导致检索为空的问题。",
 ["agent_rag_design.txt", "agent_troubleshooting.txt"])
L[100] = ("agent_about_rag", "react_agent", KB,
 "在低资源设备上可能会慢，因此要做优化。系统已部署Ollama加Qwen2.5-0.5B本地模型，技术难点正是如何保证本地模型在低资源设备上实时响应，解决办法是量化模型加流式输出；资源不足时也可改用更小的模型。",
 ["agent_rag_design.txt", "hardware_troubleshooting.txt"])
L[101] = ("agent_about_rag", "react_agent", KB,
 "不会崩溃。工具调用失败时Agent会记录错误日志，并尝试降级回答，例如直接使用知识库检索结果或返回友好提示，不会导致整个系统崩溃。问题太复杂时也可以通过上下文记忆与检索结果拆解处理，而不是让系统直接失败。",
 ["agent_troubleshooting.txt"])
L[102] = ("agent_about_rag", "react_agent", KB,
 "不会导致系统崩溃。Agent会记录错误日志，并尝试降级回答，例如直接使用知识库检索结果或返回友好提示。历史记录中2026-05-20曾出现天气工具调用失败（Ollama服务响应超时timeout=20s），降级策略是使用缓存中的历史平均数据回答用户。",
 ["agent_troubleshooting.txt"])
L[103] = ("agent_about_rag", "react_agent", KB,
 "从三方面入手：一是系统提示词约束加工具描述清晰，避免工具调用错误；二是检索增强，知识库检索为空时通过增加同义词词典优化，让关键词更容易命中文档；三是保证本地模型实时响应，用量化模型加流式输出。",
 ["agent_rag_design.txt", "agent_troubleshooting.txt"])
L[104] = ("hardware_troubleshoot", "react_agent", KB,
 "Ollama超时的诱因有三类：模型加载耗时过长、系统资源不足、本地网络配置异常。解决方案：用OLLAMA_LOAD_TIMEOUT=30m ollama serve调整加载超时；把客户端timeout设为60-120秒；用chown -R ollama:ollama修复模型目录权限；用netstat和systemctl核查服务状态；用nvidia-smi和top监控资源。",
 ["ollama_troubleshooting.txt"])
L[105] = ("agent_capability", "react_agent", KB,
 "可以。远程控制通过TCP指令实现，例如发送“CMD_LED_1_1_END”即可打开LED1；上位机界面右侧还设有远程控制面板，可分别控制3个LED和蜂鸣器，因此无需到现场就能操作灯光与声音设备。",
 ["alert_rules_and_examples.txt"])
L[106] = ("agent_capability", "react_agent", KB,
 "可以多轮对话。系统采用上下文记忆机制，短期滑动窗口保留最近5轮对话，长期记忆存储重要事件摘要，避免token爆炸；因此连续追问时它能沿用之前的上下文，不会每轮都从零开始理解你的问题。",
 ["agent_rag_design.txt"])
L[107] = ("agent_about_rag", "react_agent", KB,
 "文档没有给出下载地址。系统部署方式是本地Ollama加Qwen2.5-0.5B模型，通过API调用；从缓解超时的建议“运行一次ollama run qwen2.5:0.5b-instruct预热”可以确认模型是通过Ollama按名称拉取的。YOLO模型文件yolov8n.pt需放在项目根目录。",
 ["agent_rag_design.txt", "hardware_troubleshooting.txt"])
L[108] = ("agent_capability", "react_agent", KB,
 "当前部署的是Ollama加Qwen2.5-0.5B，通过API调用，更换模型需要在Ollama侧拉取并切换模型名。若超时频繁，官方建议考虑使用更小的模型（如qwen2.5:0.5b）。另有案例#67记录系统版本更新后Agent支持工具调用并测试通过。",
 ["agent_rag_design.txt", "hardware_troubleshooting.txt", "slope_cases.txt"])
L[109] = ("agent_about_rag", "react_agent", KB,
 "系统以本地运行为主，知识库、模型与对话记忆都在本机，系统简介明确纯本地部署、数据不公网传输。文档没有提到会外传或用于训练的机制；对话记录会写入本地会话表，超过10轮自动摘要后存入长期记忆。",
 ["system_intro.txt", "agent_rag_design.txt"])
L[110] = ("agent_about_rag", "react_agent", [],
 None, [])
L[111] = ("principle_knowledge", "react_agent", KB,
 "基于FMCW原理。毫米波雷达发射调频连续波，通过回波与发射信号的差频计算距离，测量的是边坡表面至雷达的距离，单位厘米；连续监测即可得到位移变化速率，再结合判据区分正常波动与真实滑坡。",
 ["slope_monitoring_principles.txt"])
L[112] = ("principle_knowledge", "react_agent", KB,
 "靠位移判据区分。正常需同时满足：全坡面95%以上监测点距离变化在正常范围、平均位移10秒内变化小于2mm、无连续5个以上监测点同时超限、位移曲线平稳无突变；若5%至15%监测点超限或10秒变化2至5mm为轻微滑动（黄色预警）；超过15%超限或10秒变化大于5mm或出现大片连通滑动区则为严重滑动。",
 ["displacement_criteria.txt"])
L[113] = ("principle_knowledge", "react_agent", KB,
 "来自工程标准。该比例用于判定坡面整体是否稳定，工程依据是《建筑边坡工程监测技术标准》（DBJ50/T-445-2023）中“监测点合格率≥95%判定为稳定”；判据中同时要求无连续5个以上监测点同时超限，依据是《露天矿边坡监测预警体系》中“局部连续异常点不超过5个判定为非滑动”。",
 ["displacement_criteria.txt"])
L[114] = ("principle_knowledge", "react_agent", KB,
 "10秒内变化小于2mm算正常。依据是毫米波雷达边坡监测工程常用的短期微变形阈值，短期监测取2mm/10s为正常波动上限；若10秒变化在2至5mm属轻微滑动（Ⅲ级黄色预警），超过5mm则判定为失稳前兆，属危险级。",
 ["displacement_criteria.txt"])
L[115] = ("principle_knowledge", "react_agent", KB,
 "因为曲线平稳说明没有突变。判据把“位移曲线平稳、无突变”列为正常（安全）的判定标准之一，工程依据是《土质边坡滑坡灾害分级预警判据》中“位移速率稳定、无突变为安全状态”；相反，曲线明显快速上升或下降即斜率突变，被视为滑坡最直接的判据。",
 ["displacement_criteria.txt"])
L[116] = ("principle_knowledge", "react_agent", KB,
 "意味着严重滑动、属危险报警级。超过15%监测点同时超出安全阈值，依据AQ/T 2063-2018《金属非金属露天矿山高陡边坡安全监测技术规范》属红色预警（危险级）；此时应结合位移速率与降雨情况按红色预警响应，即紧急撤离人员并实施全面交通管制。",
 ["displacement_criteria.txt", "early_warning_standards.txt"])
L[117] = ("principle_knowledge", "react_agent", KB,
 "指一整片区域同时向同一方向偏移，说明变形区已经贯通。判据把出现大片连通滑动区域列为严重滑动（危险/报警）的判定标准之一，工程依据是《土坡地震响应数值模拟中的失稳判据研究》中“塑性区/变形区贯通判定为滑动”；相比之下，小片连续超限区只是变形初期特征。",
 ["displacement_criteria.txt"])
L[118] = ("principle_knowledge", "react_agent", KB,
 "雨量是重要触发因素。案例2三峡库区小岩头滑坡的触发阈值为单次降雨雨量达70mm时边坡开始出现明显形变，累计位移-时间曲线呈典型“暴雨阶跃”；宝鸡“7·16”案例记录监测点形变速度与强降雨、持续降雨时长呈显著正相关。此外案例#11显示暴雨85mm时已触发橙色预警。",
 ["slope_failure_cases.txt", "slope_cases.txt"])
L[119] = ("principle_knowledge", "react_agent", KB,
 "采用多源数据融合。滑动窗口取最近5次距离的中位数滤波；当距离下降且监测区域内同时有人或车时触发高级告警；北斗坐标用于在地图上标出边坡点位辅助应急决策。案例中也体现了融合判断，例如#55综合位移速率与历史降雨案例给出橙色预警。",
 ["slope_monitoring_principles.txt", "slope_cases.txt"])
L[120] = ("principle_knowledge", "react_agent", KB,
 "是把累计位移随时间变化画成的曲线。它能反映边坡处于哪个变形阶段，例如卧沙溪滑坡的次级滑体变形分为初始变形、等速变形、加速变形三大阶段，加速阶段又可分为初加速、中加速、加加速；三峡库区案例中该曲线呈现典型“暴雨阶跃”特征。",
 ["slope_failure_cases.txt"])
L[121] = ("principle_knowledge", "react_agent", KB,
 "因为滑坡有滞后与累积效应。监测数据表明形变速度与强降雨、持续降雨时长显著正相关，雨水入渗使坡体强度逐渐降低；三峡库区案例中单次降雨70mm时边坡才开始明显形变，且从加速变形到破坏仅间隔数小时，说明暴雨后可能存在数小时到数天的滞后期。",
 ["slope_failure_cases.txt"])
L[122] = ("principle_knowledge", "react_agent", KB,
 "弹性变形是可恢复的，永久变形不可恢复。案例#27记录位移回缩、速率-0.03mm/h，结论是“位移回缩，属弹性变形，无危险”；而累计位移持续增大、速率保持正值则属于永久变形，是滑坡发展的标志。",
 ["slope_cases.txt"])
L[123] = ("principle_knowledge", "react_agent", KB,
 "指边坡自监测开始以来的位移累计总量。案例中持续跟踪累计位移并分级，例如宝鸡“7·16”滑坡水平位移超80mm、垂直位移超320mm、合位移超300mm；古巴滑坡2007至2023年累计最大位移达1.37米，处于初加速变形阶段。",
 ["slope_failure_cases.txt"])
L[124] = ("principle_knowledge", "react_agent", [], None, [])
L[125] = ("principle_knowledge", "react_agent", KB,
 "以连续监测为准。系统低功耗模式下常规采样频率为1Hz（每秒一次），夜间无入侵且位移变化小于0.1mm/10min时自动进入低功耗并保持该频率；判据同样以10秒为一个短期观察窗口来评估位移变化。",
 ["slope_cases.txt", "displacement_criteria.txt"])
L[126] = ("positioning", "react_agent", KB_G,
 "北斗定位用于点位标注与辅助决策。感知层配有北斗/GPS模块，定位数据通过串口传入上位机；北斗坐标用于在地图上标出边坡点位，辅助应急决策。数据处理时还会对串口乱码等问题做校验，确保坐标可靠。",
 ["slope_monitoring_principles.txt", "system_intro.txt"])
L[127] = ("principle_knowledge", "react_agent", KB,
 "可以部分做到。系统以毫米波雷达面域位移为主要监测手段，并辅以人工巡检确认坡面状态，案例中多次记录“人工巡检，边坡表面无裂缝，监测墩稳固”“人工巡检，边坡无裂缝”。但文档没有说明系统具备专门的裂缝自动识别能力。",
 ["slope_cases.txt"])
L[128] = ("principle_knowledge", "react_agent", KB,
 "影响明显。边坡复垦区与原生植被覆盖会对雷达监测产生遮挡与信号干扰；短波长SAR数据植被穿透能力较弱，易产生严重失相干，直接导致滑坡目标识别漏判、误判频发。雷达周边高大茂密树木会形成大范围遮蔽角，造成大片监测盲区。",
 ["radar_vegetation_interference.txt"])
L[129] = ("principle_knowledge", "react_agent", KB,
 "三条措施配合：定期运维清理，常态化清理雷达观测方向内的遮挡植被，消除物理遮挡；算法优化降噪，搭载植被识别与干扰消除算法过滤植被干扰信号；多源数据校验，融合多维度监测数据交叉验证，大幅降低雷达误报与漏报概率。",
 ["radar_vegetation_interference.txt"])
L[130] = ("alert_rules", "react_agent", KB,
 "有明确依据。预警分级参考红橙黄蓝四级标准；位移判据引用《建筑边坡工程监测技术标准》（DBJ50/T-445-2023）的“监测点合格率≥95%判定为稳定”、AQ/T 2063-2018的5%~15%黄色预警与大于15%红色预警、《公路边坡毫米波雷达监测预警系统技术规范》等。",
 ["displacement_criteria.txt", "early_warning_standards.txt"])
L[131] = ("case_lookup", "react_agent", KB,
 "有。案例#8记录滑坡体已趋于稳定、降级为黄色；#102记录2025-06-12红色预警、坡脚道路封闭、无人员伤亡，成功避免灾害；#104也记录红色预警后滑坡未发生、及时响应避免了损失。案例#12至#13则是一次真实发生的位移事件：2024-06-19红色预警后，2024-06-20发生小型滑坡，土石约30立方米，无人伤亡。",
 ["slope_cases.txt"])
L[132] = ("case_lookup", "event_query", KB,
 "有，主要是树叶遮挡与动物触发。案例#15记录2024-08-10大风天气下速率跳变被判定为误报，原因是树叶遮挡雷达，清理后数据恢复；#16记录猫进入监测区域触发入侵告警，人工确认无威胁后忽略；#43夜间红外检测到野猫触发告警，经验证无威胁。",
 ["slope_cases.txt"])
L[133] = ("case_lookup", "react_agent", KB,
 "2024年3月有一段完整的位移加速过程。案例#4（2024-03-05）位移0.26m、速率+0.05mm/h、中雨35mm，黄色预警加强监测；#5（03-06）位移0.28m、速率+0.08mm/h，黄色预警持续；#6（03-07）位移0.33m、速率+0.15mm/h，橙色预警建议现场巡查；#7（03-08）位移0.41m、速率+0.22mm/h，红色预警并立即封闭坡脚道路。",
 ["slope_cases.txt"])
L[134] = ("case_lookup", "react_agent", KB,
 "有。案例#102记录2025-06-12因降雨触发红色预警，位移0.40m、速率+0.09mm/h、小雨15mm，坡脚道路封闭且无人员伤亡，成功避免灾害；#101记录2025-12-01大雪导致太阳能板被积雪覆盖、系统断电约8小时。按“去年”推算即2025年。",
 ["slope_cases.txt"])
L[135] = ("case_lookup", "react_agent", KB,
 "有可参照的降雨型案例。#11（2024-06-18）暴雨85mm触发橙色预警，并提示历史相似案例#5导致滑坡；#55（2026-06-08）大雨60mm触发橙色预警，历史案例#11、#24、#46显示类似降雨易滑坡，建议巡查；#82（2026-08-18）大雨68mm橙色预警，历史案例#55类似情况未发生滑坡，但仍需注意。",
 ["slope_cases.txt"])
L[136] = ("case_lookup", "react_agent", KB,
 "从记录看动物触发更常见。案例#16猫进入监测区域、#43夜间红外检测到野猫、#68夜间动物触发红外，均经验证无威胁；人触发则多为养护或施工人员，如#28施工人员已登记忽略、#52确认为养护人员已记录。可见入侵告警绝大多数经人工确认无威胁。",
 ["slope_cases.txt"])
L[137] = ("case_lookup", "event_query", KB,
 "有。案例#17记录ESP8266断线重连、数据中断5分钟后自动恢复；#32记录北斗串口数据中断30秒后恢复、疑似接触不良；#101记录太阳能板被积雪覆盖导致断电约8小时，雪停后自动恢复供电、数据补传正常。",
 ["slope_cases.txt"])
L[138] = ("case_lookup", "react_agent", KB,
 "经历过。低温方面，案例#20记录2025-01-05小雪天气下低温导致雷达噪声增加、滤波参数已调整；#101记录2025-12-01大雪覆盖太阳能板造成断电约8小时，雪停后自动恢复。高温方面文档没有专门记录。",
 ["slope_cases.txt"])
L[139] = ("case_lookup", "react_agent", KB,
 "有专门案例。陕西宝鸡“7·16”暴雨诱发滑坡（2024年）：暴雨后监测站水平位移超80mm、垂直位移超320mm、合位移超300mm；形变特征是滑坡体中部与底部位移变化最显著，监测站出现明显下沉和倾斜；监测点形变速度与强降雨、持续降雨时长呈显著正相关。",
 ["slope_failure_cases.txt"])
L[140] = ("case_lookup", "react_agent", KB,
 "三峡库区小岩头滑坡（2021年）特点鲜明：8月28日凌晨发生整体滑移，造成3户农房倒塌、320人出行受影响；从加速变形阶段到最终滑坡破坏仅间隔数小时，突发性极强；累计位移-时间曲线呈典型“暴雨阶跃”特征；触发阈值为单次降雨雨量达70mm时边坡开始明显形变。",
 ["slope_failure_cases.txt"])
L[141] = ("case_lookup", "react_agent", KB,
 "有，且属弹性变形。案例#27记录2025-08-05雷阵雨28mm后位移0.35m、速率-0.03mm/h，结论为位移回缩、属弹性变形、无危险；#58记录位移0.61m、速率-0.02mm/h，边坡处于亚稳定状态；#65、#74、#92等记录滑坡体停止位移后降级为黄色并继续监测。",
 ["slope_cases.txt"])
L[142] = ("case_lookup", "event_query", KB,
 "系统日志中会输出“WiFi连接断开，尝试重连...”提示，TCP线程检测到连接异常后每隔3秒尝试重连服务器，直至成功。案例#17记录了2024-09-15的一次ESP8266断线重连，数据中断约5分钟后自动恢复；#41记录固件升级后连接更稳定、未再出现断线。",
 ["hardware_troubleshooting.txt", "slope_cases.txt"])
L[143] = ("case_lookup", "react_agent", KB,
 "有记录。案例显示2024-10-20雾天YOLO准确率下降至70%，启用图像增强（convertScaleAbs加All-in-One Network）后恢复至85%。优化方案为：图像预处理增加All-in-One Network去雨去雾；置信度阈值CONF_THRESH从0.45降至0.35；增加连续帧检测，连续3帧检测到目标才告警，减少误报。",
 ["optimization_cases.txt"])
L[144] = ("case_lookup", "event_query", KB,
 "有。案例#43记录2026-04-12夜间红外摄像头检测到野猫触发入侵告警，经验证无威胁；#68记录2026-07-08夜间动物触发红外，入侵告警后自动忽略；#79也记录了夜间模式摄像头红外补光正常。",
 ["slope_cases.txt"])
L[145] = ("case_lookup", "event_query", KB_D,
 "有密集记录。2026-07-04当天：12:00:21入侵检测到监测区域内有人或动物；12:21与12:22有趋势预警，距离从1.20m降至1.14m等；随后12:22:06起连续出现距离过近记录（0.29m、0.26m、0.14m、0.08m、0.05m、0.07m），属于边坡异常告警频繁触发的一天。",
 ["cases.txt"])
L[146] = ("case_lookup", "event_query", KB,
 "有。案例#95记录2026-09-20系统自检、所有模块正常；#31记录2026-02-10例行系统自检，Agent与RAG服务正常；#59记录设备自检正常、RAG向量库已更新至58条案例。文档中未记录自检发现异常的具体条目。",
 ["slope_cases.txt"])
L[147] = ("case_lookup", "react_agent", KB,
 "有。案例#15记录2024-08-10大风天气下位移速率出现+0.18mm/h跳变，判定为误报，原因是树叶遮挡雷达，清理后数据恢复；雷达干扰资料也说明植被会形成遮蔽角与监测盲区，导致数据异常。",
 ["slope_cases.txt", "radar_vegetation_interference.txt"])
L[148] = ("case_lookup", "event_query", KB,
 "按案例库记录，最近一次红色预警是2026-09-07：当天位移0.74m、速率+0.05mm/h、中雨28mm，结论为红色预警、坡脚道路封闭；随后2026-09-08位移趋稳降级为黄色。更晚的#99（2026-10-03）同样是红色预警、封闭坡脚。",
 ["slope_cases.txt"])
L[149] = ("case_lookup", "react_agent", KB,
 "案例中滑坡前的速率大致在0.05至0.31mm/h区间。案例#13在2024-06-20发生小型滑坡（土石约30立方米、无人伤亡），前一天#12速率为+0.12mm/h；#7速率为+0.22mm/h时判定红色预警；#56速率为+0.05mm/h即红色预警。宝鸡案例则记录形变速度与强降雨正相关。",
 ["slope_cases.txt", "slope_failure_cases.txt"])
L[150] = ("case_lookup", "event_query", KB,
 "没有发现“发生滑坡但系统未预警”的记录。案例库中滑坡事件前均有预警记录，例如#12（2024-06-19）红色预警、立即疏散，次日#13发生小型滑坡，土石约30立方米、无人伤亡；#102、#104等也显示红色预警后成功避免灾害。",
 ["slope_cases.txt"])
L[151] = ("optimization_extend", "react_agent", KB,
 "不能随便调。告警阈值固化在代码中，例如0.3米写在ALERT_DISTANCE变量里，当前版本不支持用户自定义，如需调整要修改代码后重新编译；冷却时间和滑动窗口参数同样由系统内置。未来版本计划开放配置界面。",
 ["alert_rules_and_examples.txt"])
L[152] = ("optimization_extend", "react_agent", KB,
 "当前版本为单点监测，不能在软件里直接新增监测点。要实现多点监测，需要部署多套系统：每套包含独立的雷达、摄像头与采集模块，再分别接入各自的上位机或统一平台。文档明确“当前版本为单点，可通过部署多套系统扩展”。",
 ["alert_rules_and_examples.txt"])
L[153] = ("optimization_extend", "react_agent", KB,
 "按原因对症优化：树叶、飞虫遮挡雷达则清理现场并增加滑动窗口滤波；YOLO误检则调整监测区域或降低置信度阈值；通信瞬时中断则增加自动重连机制，并在告警前确认连续多次异常。另外可结合连续3次异常才告警的滑动窗口与10秒冷却时间抑制误报。",
 ["alert_rules_and_examples.txt"])
L[154] = ("optimization_extend", "react_agent", KB,
 "问题偏方案设计：文档给出的图像增强手段是常规的convertScaleAbs(alpha=1.4, beta=20)与深度增强All-in-One Network，后者可同时去雨、雾、雪。若要更换算法，需要在代码中替换增强模块并重新验证检测准确率；文档未提供算法切换的配置接口。",
 ["slope_monitoring_principles.txt"])
L[155] = ("optimization_extend", "react_agent", [],
 None, [])
L[156] = ("optimization_extend", "react_agent", [],
 None, [])
L[157] = ("optimization_extend", "react_agent", [],
 None, [])
L[158] = ("vision_yolo", "react_agent", KB,
 "重新平衡阈值。调低置信度阈值（如从0.45降到0.35或0.2-0.3）能提高召回，但会引入更多误检，需要同步增加连续帧检测（连续3帧检测到目标才告警）来减少误报；也可以适当回调阈值，或调整监测区域把常误检区域排除在(160,100,480,380)之外。",
 ["optimization_cases.txt", "yolo_troubleshooting.txt", "alert_rules_and_examples.txt"])
L[159] = ("data_management", "react_agent", [],
 None, [])
L[160] = ("optimization_extend", "react_agent", [],
 None, [])
L[161] = ("optimization_extend", "react_agent", [],
 None, [])
L[162] = ("optimization_extend", "react_agent", KB,
 "已有夜间低功耗机制：夜间无入侵且位移变化小于0.1mm/10min时，系统会自动关闭显示器、采样频率降至1Hz，功耗降低60%；案例#79也记录夜间模式下摄像头红外补光正常。因此夜间使用已经做了屏幕与功耗上的优化，但界面配色主题是否可切换文档未说明。",
 ["slope_cases.txt"])
L[163] = ("optimization_extend", "react_agent", [],
 None, [])
L[164] = ("optimization_extend", "react_agent", [],
 None, [])
L[165] = ("optimization_extend", "react_agent", [],
 None, [])
L[166] = ("optimization_extend", "react_agent", [],
 None, [])
L[167] = ("data_management", "react_agent", KB,
 "随案例积累更新。知识库为本地txt文档，案例库会持续扩充，案例#70记录例行数据备份后历史案例库已扩充至70条，案例#59记录设备自检正常、RAG向量库已更新至58条案例。可见更新由运维过程驱动，文档没有给出固定周期。",
 ["slope_cases.txt"])
L[168] = ("agent_capability", "react_agent", KB,
 "可以在监测数据与知识库范围内给出建议，但不能替代专业勘察设计。系统可依据位移速率、降雨与历史相似案例给出关注、巡查、封闭坡脚等分级建议，例如#55提示类似降雨易滑坡、建议巡查，#51建议人工查看排水沟。",
 ["slope_cases.txt"])
L[169] = ("optimization_extend", "react_agent", [], None, [])
L[170] = ("optimization_extend", "react_agent", [], None, [])
L[171] = ("ops_instructions", "react_agent", KB,
 "先确认再处置。立即查看当前距离与位移速率：若距离小于0.3米或速率明显上升，按红橙黄蓝等级响应，必要时封闭坡脚道路并撤离人员；同时判断是人是动物，若为养护人员或猫狗可登记后忽略。不要忽略真实的位移异常。",
 ["alert_rules_and_examples.txt", "slope_cases.txt"])
L[172] = ("hardware_troubleshoot", "react_agent", [],
 None, [])
L[173] = ("ops_instructions", "react_agent", [],
 None, [])
L[174] = ("ops_instructions", "react_agent", KB,
 "文档没有单独的版本查看章节。案例中记录过固件与软件版本变更，例如#41的ESP8266固件升级、#67的系统版本更新并测试Agent工具调用、#29的系统升级后工具调用测试通过。实际操作可在上位机界面与程序日志中查看运行版本，固件版本需通过烧录工具读取。",
 ["slope_cases.txt"])
L[175] = ("ops_instructions", "react_agent", [],
 None, [])
L[176] = ("ops_instructions", "react_agent", [],
 None, [])
L[177] = ("ops_instructions", "react_agent", KB,
 "可间接验证：一是用网络调试助手连接192.168.4.1:8080发送GET_DIST\\n，确认距离通道能正常返回数据；二是让雷达前方出现遮挡或接近阈值，观察是否触发距离告警；三是把目标放入监测区域(160,100,480,380)内观察入侵告警。注意告警冷却时间为10秒，测试需留出间隔。",
 ["hardware_troubleshooting.txt", "alert_rules_and_examples.txt"])
L[178] = ("ops_instructions", "react_agent", KB,
 "注意供电、信号与安全。笔记本要连上ESP8266_AP热点（默认密码12345678），避免墙体遮挡以保证RSSI不低于-80dBm；设备建议独立电源而非USB供电，USB接口松动或供电不足会导致摄像头卡顿；户外操作注意防水防潮与人身安全，并保证北斗天线置于室外空旷、金属面朝天空。",
 ["hardware_troubleshooting.txt"])
L[179] = ("ops_instructions", "react_agent", KB,
 "需要重新烧录AP模式固件或修改固件中的热点配置。默认热点名ESP8266_AP、密码12345678，若看不到热点可重启模块或重新烧录AP模式固件；修改名称与密码应在固件代码中对应参数上调整后重新烧录，并同步更新上位机连接的SSID与密码。",
 ["hardware_troubleshooting.txt"])
L[180] = ("ops_instructions", "react_agent", KB_D,
 "先在软件里做量化分析。查看当前距离与位移速率，对比历史记录判断是否达到阈值；结合数据融合策略，注意当距离下降且监测区域内有人或车时会触发高级告警；再检索历史相似案例做参照，例如#34、#81案例中检索到相近案例后判定风险较低。",
 ["slope_monitoring_principles.txt", "slope_cases.txt"])
L[181] = ("ops_instructions", "event_query", [],
 None, [])
L[182] = ("ops_instructions", "react_agent", KB,
 "要注意安全与恢复。植被遮挡会造成雷达遮蔽角与监测盲区，清理属于基础运维，但案例#15显示清理后数据才恢复正常；清理时避免正对雷达主波束造成误报，注意坡面作业人身安全、防滑防坠落；清理后应复测距离数据，确认读数恢复正常。",
 ["radar_vegetation_interference.txt", "slope_cases.txt"])
L[183] = ("ops_instructions", "react_agent", KB,
 "可以。用网络调试助手连接到192.168.4.1:8080，发送GET_DIST\\n即可读取雷达距离；同时可检查Serial串口是否输出原始距离值。若读数恒为-1.0，说明未检测到有效回波，需排查遮挡、量程或模块初始化与通信协议问题。",
 ["hardware_troubleshooting.txt"])
L[184] = ("ops_instructions", "react_agent", KB,
 "结合图像与记录判断。YOLO检测类别0为人、15为猫、16为狗，只要位于监测区域(160,100,480,380)内都会触发入侵告警；可查看告警时的摄像头画面确认目标形态，并对照历史记录，例如猫、狗或养护人员通常登记后忽略，而真实人员闯入才需处置。",
 ["alert_rules_and_examples.txt", "slope_cases.txt"])
L[185] = ("ops_instructions", "react_agent", KB,
 "滤波参数用于抑制噪声。系统采用滑动窗口滤波器，取最近5次距离的中位数；低温环境下雷达噪声增加时需要调整滤波参数，案例#20记录小雪导致雷达噪声增加、滤波参数已调整；若误报偏多可加大滑动窗口并配合连续3次异常才告警的策略。",
 ["slope_monitoring_principles.txt", "slope_cases.txt"])
L[186] = ("support", "react_agent", [],
 None, [])
L[187] = ("ops_instructions", "react_agent", [],
 None, [])
L[188] = ("ops_instructions", "react_agent", [],
 None, [])
L[189] = ("ops_instructions", "react_agent", KB,
 "可做三步验证：启动程序后扫码登录微信，确认自动弹出的微信登录二维码已成功登录；检查itchat库是否正常安装；若仍收不到，重启程序重新登录。也可触发一次告警测试，观察微信文件传输助手与QQ邮件是否送达。",
 ["hardware_troubleshooting.txt", "system_intro.txt"])
L[190] = ("onboarding_advice", "react_agent", KB,
 "最核心的是按层接通并验证数据链路：先确认C8T6、毫米波雷达、摄像头、温湿度与北斗模块正常供电，串口有原始距离值；再连上ESP8266_AP热点，用网络调试助手发送GET_DIST\\n确认距离通道；然后熟悉告警规则（距离小于0.3米告警、冷却10秒）与红橙黄蓝预警响应，并做好雷达前方遮挡清理与设备防水维护。",
 ["system_intro.txt", "hardware_troubleshooting.txt", "alert_rules_and_examples.txt"])


def main():
    qs = json.load(open(QS, encoding="utf-8"))
    valid_routes = {"react_agent", "event_query", "sensor_check", "trend_analysis",
                    "slip_judgment", "self_info", "fact_write", "note_write",
                    "casual", "compound"}
    valid_tools = {"tool_get_distance", "tool_get_gps", "tool_get_weather", "tool_search_kb"}
    docfiles = set(os.listdir(DOCS))

    rows = []
    problems = []
    for it in qs:
        i = it["id"]
        if i not in L:
            problems.append(f"id {i} missing label")
            continue
        tt, route, tools, ans, srcs = L[i]
        if route not in valid_routes:
            problems.append(f"id {i} bad route {route}")
        for t in tools:
            if t not in valid_tools:
                problems.append(f"id {i} bad tool {t}")
        grounded = ans is not None
        if grounded:
            if not srcs:
                problems.append(f"id {i} grounded but no sources")
            for s in srcs:
                if s not in docfiles:
                    problems.append(f"id {i} source missing: {s}")
        row = {
            "id": i,
            "query": it["query"],
            "task_type": tt,
            "expected_route": route,
            "expected_tools": tools,
            "reference_answer": ans,
            "reference_sources": srcs if grounded else [],
            "grounded": grounded,
            "reviewed": False,
        }
        rows.append(row)

    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"written {len(rows)} rows -> {OUT}")
    for p in problems:
        print("PROBLEM:", p)
    # 字数分布
    short = [(r["id"], len(r["reference_answer"])) for r in rows
             if r["reference_answer"] and not (80 <= len(r["reference_answer"]) <= 200)]
    print("answers outside 80-200 chars:", short)


if __name__ == "__main__":
    main()
