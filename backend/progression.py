"""Original staged syllabus. Stage numbers describe learning scope, not qualifications."""
from curriculum import TOPICS

EVERYDAY = [
 ('在餐厅说清需求','数量词、もう一つ、不要某物时的礼貌表达'),
 ('约定时间与地点','时间に、地点で；确认约定'),
 ('描述房间的位置','上・下・中・隣；あります／います复习'),
 ('比较两个选择','〜より、〜のほうが；简单比较'),
 ('谈天气与心情','形容词的现在与过去表达'),
 ('说明日常习惯','频率词いつも・ときどき；ます形复习'),
 ('生活场景综合复习','位置、时间、喜好与比较'),
 ('正在做什么','〜ています：正在进行的动作'),
 ('请求与许可','〜てもいいですか；礼貌请求'),
 ('告诉对方不能做什么','〜てはいけません；理解规则'),
 ('连接两件事情','动词て形连接先后动作'),
 ('说明一个简单原因','〜から；解释个人选择'),
 ('旅行中的小问题','用已学句型说明困难并请求帮助'),
 ('生活表达阶段回顾','按本阶段例句串联会话，不引入未学表达')
]
CONNECTED = [
 ('介绍一次经历','〜たことがあります；经历与具体过去的区别'),
 ('说明我的打算','〜つもりです；表达计划'),
 ('提出温和建议','〜たほうがいいです；避免过度命令'),
 ('尝试一件新事','〜てみます；提出尝试'),
 ('说出自己的能力','可能表达入门；できる'),
 ('礼貌地解释情况','〜んです入门；结合场景解释'),
 ('经历与计划综合复习','复用本阶段句型完成短对话'),
 ('用句子修饰名词','基础名词修饰；わたしが読む本'),
 ('描述变化','〜くなります・〜になります'),
 ('表达自己的想法','〜と思います；区分事实与个人观点'),
 ('转述简单信息','〜と言っていました；明确消息来源'),
 ('提出假设','〜たら入门；用生活场景表达条件'),
 ('把一件事讲清楚','按时间顺序讲述短事件'),
 ('连贯表达阶段回顾','综合经历、计划、原因与观点')
]
# Continued stages revisit these foundations in different contexts instead of
# pretending an unlimited sequence of stages automatically confers proficiency.
SCENES = ['邻里与日常','旅行与交通','饮食与购物','兴趣与文化','学习与工作','城市与自然']
PRACTICE = [
 ('介绍人物与环境','身份、名词修饰与位置'),('确认时间安排','时间、计划与沟通确认'),
 ('说清自己的选择','喜好、比较与原因'),('听懂并回应请求','请求、许可与场景礼貌'),
 ('描述发生过的事','过去时与经历'),('讲述正在做的事','进行状态与动作连接'),
 ('把前半阶段串起来','围绕前六课进行复习'),('提出建议与替代方案','建议、尝试与能力'),
 ('表达观点与依据','事实、个人观点与信息转述'),('处理意外情况','条件、解释与求助'),
 ('读懂一段短文','用已学表达提取关键信息'),('完成一段场景对话','连贯表达与沟通修复'),
 ('完成自己的小作品','结合学习目标写一段有上下文的日语'),('阶段综合回顾','按本阶段薄弱点复习')
]

def stage_for(number):
    return 1 if number <= 28 else 2 + (number - 29) // 14

def stage_info(stage):
    start=1 if stage==1 else 29+(stage-2)*14
    end=28 if stage==1 else start+13
    names={1:'入门基础',2:'生活表达',3:'连贯表达'}
    scene=SCENES[(stage-4)%len(SCENES)] if stage>=4 else ''
    name=names.get(stage,f'持续应用 · {scene}（第{(stage-4)//len(SCENES)+1}轮）')
    level={1:'零基础入门',2:'基础生活交流',3:'简单连贯表达'}.get(stage,'已学基础句型的场景迁移与巩固')
    return dict(stage=stage,name=name,start=start,end=end,total=end-start+1,level=level)

def course_spec(number):
    stage=stage_for(number); info=stage_info(stage); offset=number-info['start']
    if stage==1: title,focus=TOPICS[offset]
    elif stage==2: title,focus=EVERYDAY[offset]
    elif stage==3: title,focus=CONNECTED[offset]
    else:
        title,focus=PRACTICE[offset]
        title=SCENES[(stage-4)%len(SCENES)]+' · '+title
        focus+='；结合当前目标与近期错题复用已学内容，增加情境变化而非自动提升等级'
    return dict(lesson_no=number,day=number,stage=stage,title=title,focus=focus)

def stage_courses(stage):
    info=stage_info(stage)
    return [course_spec(n) for n in range(info['start'],info['end']+1)]
