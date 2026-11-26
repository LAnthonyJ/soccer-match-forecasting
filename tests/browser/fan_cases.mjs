// Audited matchweek 12. The assertions below are independent of the answer renderer.
export const F='2026-27_nottingham-forest_chelsea'
export const A='2026-27_arsenal_manchester-city'
export const STAMP='2026-11-26T08:00:00Z'
const ftime=/11[/-]28.{0,3}04:00|11-27T20:00/
const atime=/11[/-]30.{0,3}00:30|11-29T16:30/
const scoresF=[/1[–\-:：]1/,/13\.0%/,/2[–\-:：]1/,/8\.5%/,/1[–\-:：]2/,/8\.1%/]
const scoresA=[/1[–\-:：]1/,/13\.2%/,/1[–\-:：]0/,/10\.3%/,/2[–\-:：]1/,/9\.2%/]
const probsF=[/37\.7%/,/27\.3%/,/35\.0%/], probsA=[/44\.0%/,/27\.6%/,/28\.4%/]
function example(page,question,expected,...patterns){return {page,question,expected,patterns}}
function missing(topic,question,pattern){
  return {...example(F,question,F,pattern),missing:topic,exclude:/37\.7%|27\.3%|35\.0%/}
}
export const groups=[
  ['比赛时间',[
    example(F,'本场几点开球？',F,ftime),
    example(A,'阿森纳对曼城什么时候开球？',A,atime),
    example(F,'本页比赛时间？',F,ftime),
    example(A,'开球时间是什么时候？',A,atime),
    example(F,'本场预测的冻结窗口？',F,/11[/-]27.{0,3}04:00|11-26T20:00/),
    example(A,'本场的 T−24 截止时间？',A,/11[/-]29.{0,3}00:30|11-28T16:30/)
  ]],
  // Run these questions in sequence in a single conversation on the Forest page.
  ['跨场指代',[
    example(F,'阿森纳下一场踢谁？',A,/阿森纳/,/曼城|曼彻斯特城/,atime),
    example(F,'那场几点开球？',A,atime),
    example(F,'那场的胜平负概率？',A,...probsA),
    example(F,'那客队呢？',A,/曼城|曼彻斯特城|客胜/,/28\.4%/),
    example(F,'本页这场几点开球？',F,ftime),
    example(F,'那场最可能的比分？',F,...scoresF)
  ]],
  ['胜平负',[
    example(F,'两队为什么这么接近？',F,...probsF,/2\.7/),
    example(A,'这场更看好谁？',A,...probsA),
    example(F,'本场打平的机会呢？',F,/27\.3%/),
    example(A,'平局概率有多高？',A,/27\.6%/),
    example(F,'主胜和客胜的概率各是多少？',F,/37\.7%/,/35\.0%/),
    example(A,'这场胜平负预测？',A,...probsA)
  ]],
  ['比分分布',[
    example(F,'这场最可能的比分是什么？',F,...scoresF),
    example(A,'预测比分前三项？',A,...scoresA),
    example(F,'列出前三个比分的概率',F,...scoresF),
    example(A,'比分分布中最可能的是哪个？',A,...scoresA),
    example(F,'两队的模型预计进球是多少？',F,/1\.41/,/1\.36/),
    example(A,'阿森纳对曼城的预期进球？',A,/1\.45/,/1\.13/)
  ]],
  ['近况与交锋',[
    example(F,'切尔西过去五场战绩？',F,/11-08/,/桑德兰/,/10-10/,/伯恩茅斯/),
    example(F,'森林最近五场分别踢了谁？',F,/11-07/,/曼城/,/10-11/,/水晶宫/),
    example(A,'阿森纳的近5场比分？',A,/11-07/,/赫尔城|赫尔|侯城/,/10-10/,/利兹/),
    example(F,'上一次两队交锋比分是多少？',F,/2026-05-04/,/1[–\-:：]3/),
    example(F,'切尔西对诺丁汉森林上次交手？',F,/2026-05-04/,/1[–\-:：]3/),
    example(F,'两队近期的进攻表现如何？',F,/1\.03/,/1\.45/)
  ]],
  ['积分榜',[
    example(F,'切尔西本赛季积分榜第几？',F,/切尔西/,/16\s*分|积分\s*16/,/已确认/),
    example(F,'诺丁汉森林的联赛积分？',F,/森林/,/9\s*分|积分\s*9/),
    example(A,'阿森纳现在排第几？',A,/阿森纳/,/27\s*分|积分\s*27/),
    example(A,'曼城多少积分？',A,/曼城/,/21\s*分|积分\s*21/),
    example(F,'按已确认比分，客队有多少积分？',F,/切尔西/,/16\s*分|积分\s*16/),
    example(A,'当前主队的积分榜位置？',A,/阿森纳/,/27\s*分|积分\s*27/)
  ]],
  ['市场与评分',[
    example(F,'市场也这么看吗？',F,/缺少|暂无|不足/,/报价|市场/),
    example(A,'目前赔率分歧多大？',A,/缺少|暂无|不足/,/报价|市场/),
    example(F,'模型 B 和市场的差距？',F,/缺少|暂无|不足/,/报价|市场/),
    example(A,'独立模型与盘口相比呢？',A,/缺少|暂无|不足/,/报价|市场/),
    example(F,'模型本场评分？',F,/暂无|尚无|没有|缺少/,/评分|计分/),
    example(A,'这场有单场评分吗？',A,/暂无|尚无|没有|缺少/,/评分|计分/)
  ]],
  ['无资料',[
    missing('injuries','这场有伤停消息吗？',/伤停/),
    missing('injuries','周末哪支球队的首发变了？',/首发/),
    missing('weather','天气会影响两队胜率吗？',/天气/),
    missing('tactics','两队战术布置会怎样影响平局概率？',/战术/),
    missing('player','谁可能进球？',/球员/),
    missing('live','这场现在几比几？',/实时比分|即时比分/)
  ]]
]
