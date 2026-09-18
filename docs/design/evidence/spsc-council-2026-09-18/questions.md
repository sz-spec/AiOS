# Committee question index — user request, 2026-09-18

For each: `[ID] תשובה: ...` / `ראיה: path:line, hash or command` / `ביטחון: גבוה/בינוני/נמוך`; if unknown identify experiment and duration. Governance not evidenced is `להחלטת CTO`.

T1 pure guest compute loop without syscalls slowdown between17/18, ratio? T2 SPSC push/pop syscall or sharedmemory? T3 runnable taskcount/identities duringtimedwindow? T4 historicalhost changes QEMU/OS/reboot/power/concurrency? T5 whichfailedprogram+exit, whybench_ai_throughput exit0nearFAIL?

S1 emptyconsumerbehavior/timequantumticks? S2 producervyield guaranteesconsumernext?runqueuepolicy? S3 actual1024fillguesttimeeachday/cross10ms? S4 involuntaryproducerpreemptionsvsvoluntaryyieldcount? S5 capacity512/2048rategradualvsstep? S6 emptyconsumer-yieldrate andmeaningif80K? S7 actual100Hz/numbertimerIRQswindow?
K1 earlierGETPIDvsreportedlate, costgrowsduringsuite/whataccumulates? K2 GETPIDentry/dispatch/exit/root-sync/schedulingcostdecomposition? K3 rootsyncpercallwhatandwhy? K4 GETPIDn/median/p99/tailvsuniform? K5 newusercopyinSPSChotpathorSYSINFOonly? K6 isolatedearlybootSPSC/GETPIDcost?
Q1 historicalQEMUversion+binaryhashequality? Q2 completecommandidentityaccel/cpu/smp/tbcache? Q3 TSC/guesttimeunderTCGhostwallclock? Q4 fixedshifticountA/Bstability? Q5 guesttimevsmonotonichosttimewindow/tickloss/catchup? Q6 TBflush/retranslationevidence?
H1 hostCPU, AppleSiliconP/Eplacement/QoS? H2 concurrentbuild/hosttests/PDF/QEMU/agentworkbothdays? H3 power/thermalbothdays? H4 OSupdate/reboot/backgroundchanges? H5 oldISOquiettoday5repeats? H6 availableauthorizedsecondmachine?
M1 IPCthroughputvsresponsivenessboth? M2 sameconditiondistribution,nrequiredtodetect5%? M3 one-second/10ms-resolution/~40-88capacityequivalentsstable? M4 origin/calibration80Kthreshold? M5 purecompute referencewithsameguest? M6 historicalbimodalvscontinuous/intermediatevalues?
V1 samefailedprogramexitallfour18thruns? V2 whichvalidatorconditionmissing/nonzero/duplicate/reorder? V3 observedFAIL+zeroexitprograms/howmany,17th57/57basedonlyexit? V4 orderidentical,immediatepredecessorhealth,leftoverliveprocesses? V5 NPUunavailablepass/skip/failcount? V6 raw17thlogshashesretained?
A1 SYSINFOformalstatusverified/merged/blockeddecisionauthority? A2 nativeandmusl99callers/binariesbehaviorENOSYS? A3 partialcopyassumptions/all-or-nothingcaller/documented? A4 othersyscallunsafe-fixedstructpatternaudited? A5 futuretestfailsifsysinfo/__lsysinfoaddressdiff? A6 beyondoneCPUemulatorSMP/hardware/concurrentmprotectrequirements?
G1 issueowneranddiagnosisduedate? G2 exactclosecriteria#consecutive57/57,firmware/hostconditions? G3 host/TCGcausepolicydecisiondeterministic/dedicatedHW/testdesignauthority? G4 whybase+patch,whenonecommit/tagperISO? G5 issue-registerIDsandJ0–J12journeys,RELorCAP? G6 whatisblockedvscontinues?

E1 existingpurecomputeinoldcanonicalISOrepeats(noinnerloopsyscalls),compare17thifavailable.
E2 quietoldISOminimum5runsrecordhostbefore/after; alternateold/newwherecomparing.
E3 old/newfixedshifticountA/B minimum3each requested,butgeneralrule5requires5eachforconclusion. Validateinstalledsyntax. DiagnosticcomparisonNOTgate.
E4 diagnosticconsumer-yieldonempty.
E5 diagnosticcapacity512and2048.
E6 diagnostichealthimmediatelyafterboot+GETPID+runnablecountswindow.
E7 diagnosticlightfill/drain,tickpreempt/yieldcounts,timerIRQcount,paireduninstrumentedrun.
E8 diagnosticGETPIDphasecost+fullsamplesearlyandlate.

No canonical threshold80K/window1000ms/capacity1024 changes. All failures immutable. During EVERY performance run no other agent host work (including inspections/reportwriting) except minimalmeasurementharness+requiredtelemetry; waitonly. Record pre/post hostload/heavyprocesses/power/thermalifavailable/QEMUversion+hash/completecommand/ISOhash. Five repetitions/configforconclusions, allvalues/min/median/max. LabeldiagnosticISOsseparatehashes/non-gate. RegisterpredictionsBEFOREeachE. AllE1–E8 inpriorityorder. Stopandreportcontradictionsratherthanblindcontinuation. v1.0untouched,newHebrewv1.1HTML/PDFandfullcommitteeanswers.
