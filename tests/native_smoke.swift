// App-level integration harness, compiled only by scripts/test_native.sh.
var qaStarted = false
var qaPolls = 0
var qaTimer: Timer?
func finishQA(_ result:[String:Any]) {
    let output=URL(fileURLWithPath:ProcessInfo.processInfo.environment["HARU_QA_RESULT"]!)
    if let data=try? JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys]){try? data.write(to:output,options:.atomic)}
    qaTimer?.invalidate()
    NSApp.terminate(nil)
}
func startQA(_ delegate:HaruApp) {
    qaTimer=Timer.scheduledTimer(withTimeInterval:0.3,repeats:true){_ in
        qaPolls += 1
        if qaPolls > 120 {finishQA(["ok":false,"error":"native bootstrap timeout"]);return}
        guard delegate.web != nil else{return}
        if !qaStarted {
            delegate.web.evaluateJavaScript("typeof state !== 'undefined' && !!state") {value,error in
                guard value as? Bool == true,!qaStarted else{return}; qaStarted=true
                let code="""
                (async()=>{try{
                  const before=await rpc('bootstrap');
                  const aiSettings=await rpc('config_get');
                  if('key' in aiSettings||typeof aiSettings.key_configured!=='boolean')throw new Error('AI 配置回显检查失败');
                  let configSaveRoute=false;
                  try{await rpc('config_save',{model:''});}catch(e){configSaveRoute=e.message.includes('模型 ID');}
                  if(!configSaveRoute)throw new Error('AI 配置保存原生接口检查失败');

                  const catalog=await rpc('curriculum');
                  const routeChecks=[];
                  for(const action of ['stage_assessment','remedial']){try{await rpc(action);}catch(e){routeChecks.push(!e.message.includes('不支持'));}}
                  await openLesson(1);
                  if(lesson!==null||document.querySelector('[data-action=builtin-lesson]')||document.querySelectorAll('.course-choice').length!==28)throw new Error('课程列表空态错误');
                  const l=await rpc('lesson',{day:1,source:'ai'});
                  await openLesson(1); setLessonTab('exercise');
                  const grade=await rpc('grade',{id:l.id,answers:[0,1,1,0,2,1,1,0]});
                  const after=await rpc('bootstrap');
                  const cards=await rpc('card_seed');
                  let randomRoute=false;
                  try{await rpc('card_random',{count:0});}catch(e){randomRoute=e.message.includes('随机生成数量');}
                  let dailyAddRoute=false;
                  try{await rpc('daily_word_add',{id:''});}catch(e){dailyAddRoute=e.message.includes('内容ID');}
                  while(dailyWordLoading)await new Promise(resolve=>setTimeout(resolve,50));
                  const dailyRoute=!!dailyWord||!!dailyWordError&&!/操作无效|不支持/.test(dailyWordError);
                  if(!dailyAddRoute||!dailyRoute||document.querySelector('.little-note'))throw new Error('今日的一点日语原生接口检查失败');
                  await navigate('cards');
                  selectedCard=cards[0].id;render();
                  const historyView=!!document.querySelector('.card-detail')&&document.querySelectorAll('#card-history .history-card').length===5;
                  const lookup=await rpc('dictionary',{word:'水'});
                  const task=await rpc('chat_start',{scene:'cafe'});
                  let deltaCount=0;
                  await streamingRPC({session:task.session,message:'水をください。',request_id:'native-complete-123456789'},()=>deltaCount++);
                  const streamed=await rpc('chat_history',{session:task.session});
                  let cancelled=false;
                  try{
                    await streamingRPC({session:task.session,message:'停止テスト',request_id:'native-cancel-123456789'},()=>{void rpc('chat_cancel',{request_id:'native-cancel-123456789'});});
                  }catch(e){cancelled=true;}
                  const afterCancel=await rpc('chat_history',{session:task.session});
                  if(lookup.word!=='水'||deltaCount<1||streamed.length!==2||afterCancel.length!==2||!cancelled)throw new Error('原生流式或取消检查失败');
                  const exported=await rpc('export',{type:'json'});
                  const grammar=await rpc('grammar_catalog',{level:'N1'});
                  if(grammar.items.length!==20)throw new Error('原生语法目录数量错误');
                  await navigate('grammar_library');
                  if(!document.querySelector('.grammar-detail'))throw new Error('原生语法页面未渲染');
                  const gp=await rpc('grammar_practice',{id:'n1-001'});
                  const gr=await rpc('study_save',{id:gp.id,revision:0,finish:true});
                  const legacyGenerated=await rpc('study_generate',{level:'N1',count:5,skill:'mixed'});
                  let generation=await rpc('study_generation_start',{level:'N1',mode:'targeted',type_id:'kanji_reading',count:5});
                  const initialGeneration=await rpc('study_generation_status',{id:generation.id});
                  if(initialGeneration.status!=='active'||initialGeneration.count!==5||initialGeneration.completed_questions!==0)throw new Error('原生分段生成初始状态错误');
                  const generationPhases=[];
                  for(let step=0;step<10&&generation.status==='active';step++){
                    generation=await rpc('study_generation_step',{id:generation.id});
                    generationPhases.push(generation.phase);
                    if(JSON.stringify(generation).includes('正解'))throw new Error('原生生成进度泄漏题目答案');
                  }
                  if(generation.status!=='complete'||generation.completed_questions!==5||!generationPhases.includes('reviewing')||!generationPhases.includes('global_review')||!generationPhases.includes('assembling'))throw new Error('原生分段命题、独立审查或全卷审查未完成');
                  const generated={id:generation.paper_id,model:generation.model};
                  const ep=await rpc('study_start',{id:generated.id});
                  const globalReview=ep.paper.generation?.global_review;
                  if(generationPhases.indexOf('global_review')>=generationPhases.indexOf('assembling')||ep.paper.global_review_model!=='fixture-global-reviewer'||globalReview?.approved!==true||globalReview.model!=='fixture-global-reviewer'||globalReview.rounds!==1||globalReview.revision_count!==0||!Number.isFinite(globalReview.reviewed_at))throw new Error('原生全卷审查元数据未保存或阶段顺序错误');
                  const saved=await rpc('study_save',{id:ep.id,revision:0,answers:{q1:0}});
                  const resumed=await rpc('study_attempt',{id:ep.id});
                  if(resumed.answers.q1!==0||gr.status!=='submitted')throw new Error('原生练习保存失败');
                  const deleted=await rpc('study_delete',{id:generated.id});
                  const preserved=await rpc('study_attempt',{id:ep.id});
                  const n1Catalog=await rpc('study_catalog',{level:'N1'});
                  if(!deleted.deleted||!deleted.history_preserved||preserved.answers.q1!==0||preserved.paper.questions.length!==5||n1Catalog.papers.some(p=>p.id===generated.id)||!n1Catalog.history.some(a=>a.id===ep.id))throw new Error('原生删除没有保留作答快照');
                  const cancelGeneration=await rpc('study_generation_start',{level:'N5',mode:'full'});
                  await rpc('study_generation_step',{id:cancelGeneration.id});
                  const generationCancelled=await rpc('study_generation_cancel',{id:cancelGeneration.id});
                  const cancelledGenerationStatus=await rpc('study_generation_status',{id:cancelGeneration.id});
                  const cancelledGenerationStep=await rpc('study_generation_step',{id:cancelGeneration.id});
                  if(generationCancelled.status!=='cancelled'||cancelledGenerationStatus.status!=='cancelled'||cancelledGenerationStep.status!=='cancelled'||generationCancelled.paper_id)throw new Error('原生取消生成失败');
                  const ec=await rpc('study_catalog',{level:'N5'});
                  if(ec.papers.length!==2||ec.papers[1].count!==91)throw new Error('官方题库错误');
                  let badAsset=false;try{await rpc('study_open_asset',{id:'../.env'});}catch(e){badAsset=e.message.includes('媒体标识');}
                  if(!badAsset)throw new Error('原生媒体路径未校验');
                  await rpc('study_open_asset',{id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.wav'});
                  await rpc('study_stop_audio');
                  await navigate('jlpt');
                  if(!document.querySelector('.study-ai'))throw new Error('原生真题页面未渲染');
                  window.__qaResult={ok:randomRoute&&historyView&&catalog.stage===1&&catalog.courses.length===28&&routeChecks.length===2&&routeChecks.every(Boolean)&&before.stats.lessons===0&&grade.score===100&&after.stats.lessons===1&&cards.length===5,streamDeltas:deltaCount,cancelPreservedTurns:afterCancel.length===streamed.length,dictionaryLookup:lookup.word,randomCardRoute:randomRoute,cardHistoryView:historyView,progressionRoutes:routeChecks.length,initialLessons:before.stats.lessons,grade:grade.score,completed:after.stats.lessons,cards:cards.length,exported:!!exported.path,rendered:document.querySelector('h1').textContent,voices:'checked separately'};
                  window.__qaResult.study={grammarPoints:grammar.items.length,officialPapers:ec.papers.length,generatedModel:generated.model,legacyGeneratedModel:legacyGenerated.model,generationQuestions:generation.completed_questions,generationPhases,globalReviewSaved:globalReview.approved,globalReviewModel:ep.paper.global_review_model,globalReviewRounds:globalReview.rounds,globalRevisionCount:globalReview.revision_count,deletedPaperAbsent:true,deletedHistoryPreserved:preserved.answers.q1===0,generationCancelled:generationCancelled.status==='cancelled',savedRevision:saved.revision,mediaPlayback:true,pathValidation:badAsset};
                }catch(e){window.__qaResult={ok:false,error:e.message}}})(); true;
                """
                delegate.web.evaluateJavaScript(code,completionHandler:nil)
            }
        } else {
            delegate.web.evaluateJavaScript("window.__qaResult || null") {value,error in
                if var result=value as? [String:Any] {
                    result["japaneseVoiceAvailable"]=AVSpeechSynthesisVoice(language:"ja-JP") != nil
                    finishQA(result)
                }
            }
        }
    }
}
