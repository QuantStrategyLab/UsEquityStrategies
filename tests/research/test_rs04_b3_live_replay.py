"""Invented inputs only; strategy parity is distinct from recorded-live parity."""
from dataclasses import replace
import base64
import hashlib
import zlib
from datetime import date

import pytest

from us_equity_strategies.research.rs04_b3 import State, advance
from us_equity_strategies.research.rs04_b3_live import (
    LiveRuleAdapter, binding_report, compare_recorded_decisions, default_config,
)
from us_equity_strategies.research.rs04_b3_evaluation import (
    historical_plan, evaluate_paths,
)
from us_equity_strategies.research.rs04_b3_runner import replay, synthetic_sessions


# Exact code-only source fixture from UES 4a3943883cd6b5bbfe32a559e56a91b40a81b7ce.
# Git blob cebea7508ed237c6c1ef90cdd2db49159e2011f8; decoded bytes are only
# hashed by the binding reader, never imported or executed as strategy code.
_SOXL_4A394388_SOURCE_ZLIB_B85 = (
    'c-q}PU2`M3aqs*U<8ir;qvh(PwfC;fIx4ldE1y-?t=5vxzN;<O)QB8P<0Uyf4!J9tR#pB%-jjz^p7NO2'
    '{Hx?A1m9=?4Gf0TIhX9iN*Z)G8jY_;ccU>~)$=6Jr<?U=Rpxn8&6o9RofL~jy)M>Oy=Z#9DTNu=vspQ2'
    'U-paZu>}0KST3u@%^<lbf83OdaoOvMlEr4eyibZISu6#KO_LL#YJHzK>s7HXZ>qA{uNLEaUgopnzFbM7'
    '{a!-;oxFX0_U&<g`s#<{^ZfkP<;hw8?b#dh;q06I^6c`}Y5wN?)t}#-{rPSF^>=T+Ildf(&@OoC^`@Fl'
    'a#(4;oD~aRcvG&;q7@YZFI&{Bc`>X0TAC2WY*vrCpa#7x(9U_W`asC9$X^6`TrbwEdd7bi<F(oHRoT?D'
    'k7aJ+EbFGC6)EImv%alY)n8fl;o3jeGXgcEUnk|P{7BnYuFD0*H0yqMc6##qhy3E~`_ug5^7vhTe*E>>'
    'cW>Y12QOac2QLq}G|rDNkKYo}zU5u=yOZya-wX`iLp4%fdp%Oc_xU%kE|2reljHN_H~H76$D|%gFc~Fj'
    'Q{Ua?H>3gix++)YB)^`Oi%HsheRh7Fzq+_MzPun{{giT=4iftFd;0Th`ZMeO>FkvB{prc&5BZzp)8jvp'
    '!EpKe^W%%(pPjw|Ow*dC)m=#lw_}{2ot~b&{U(2ReEym?|CBLGx?)DAK)x(jW7@u189|HkPP*%_<)SIq'
    'T=LsUmIui$oIVhlfBWkE5673JN545CGvxKz+e-qMzdJwsooEU=NK*^%-Py$n>-zJv?=G!|Y|~ZMd>{lS'
    'n=z3o=??aHJ)NcxS?~1tn^$CPy*@v=IJqF?F5aEJy*P#pj0t$v5d3CIW>lFsx5cFXluFFY^Y4xoI<1}J'
    'tXCVtD??0sIh~PY9UCR@nNZWtt4KNsB3{Vy!0?qIksTTj?sluh<?Gt2ye`OUHZIwsCd=?;jg(%ISvV=D'
    'Nlq4uMKhyw?NhbB&8I|$*TwilUw*GwgXBYbKS*TBAlcjd@TpkcG}%y0`^ALLQX+Vb)Mq#fQUH>jW}yfr'
    ')fC{4Xe~)OYfAM|vN)yd?^;f>{>NgrDYNAHQ9@QZ$wIT4tqB8p(d5%vO`z*lzNi<afM!svJ_cAd$y-v$'
    'kpXMz;`1UkcultgWiL5AI2i69P9J`i;*62m|C92z{0GjOu8t6e+(|;LlD-DZ_Q|}e7GystSN+T(JWZEd'
    'QVJ0sEFmnnx{%4h7C}?YmovI-Z2n#yq}s_bTG~h$>C-j};0*0!v}MR<w!S6sw>6n`lX6{DGrop{3B&&0'
    '<3BX;yq=W&8{2g>8UAr{zbNL_*!LMq*esdV^#?XFjjy`H%<uDRLCdOo(wGHAnv!+-X<RJHer<FlZ3O8?'
    'Do?XzNxe$aG}|X7)v}-M&+1RKCuCo7ywOhkm+sB$f@}<YNg+&Z)pxemflYXb%}rM_*}$YAB?<72fo)6N'
    '#s{)(BLP5dDiFSn4E$)*VIAs<qHlD#jYPUUfI>>0TFkMGAX^ZCz=QgYj1Xu1(?x(BMXZGYOkD5YWdLJy'
    '(`W%xF1fl1Cc8b!bHQKgQgG{IlROm|$rcRT_n+zE_u19Mz$%m@ixf=~HL;V_Q^Pgs_X*iOO(Lvg35Dn8'
    'XVQ7BMjtUgmKuu~9AO0AMb2D-?Ck*(W^vZrsS|x2*s=2zb*mTM9@M=@8Zm#qS!xA{FW<Jz%|T8ig~)lD'
    '_M?RSxmgO<3Qq@;EzCd0G85kFFHc_?J7jwwVf_=^H~b8y@uqxD(K~0dZCg?v0J{1*id%>>x#3rfNj0WT'
    '$eBvgSAREyq`9A8*Rvtr_n0~?ej|7VQ5;5ei4sc3vbZN|2~~zQ{ytH|`<#T_7c;tF;t-qVQbBAVU_S`j'
    '44CQHqJGr`k8(AXyD<CHt6b5KLY29Pg+g`X>K0Jf%DZv7Tqpg@`(=5&TG5ljpJ*xjJ@b<%j4YN!Ut1Il'
    'sgU`B#ortbv~xTobB1&$JJ4{h$azC|$-(}?AYq~$9qu1wPo{<%!cg)DoX(58KC7He+dc<lY-EE(X`i|o'
    '*L6KZxc^;24nnd0#%LI<5EODIUNmcZAe7`Om@8;j=trVj^MJQD%kY|k<kscgT4;y-t4|$dfYl<|riav0'
    'K+o~?=Y84GAN3-AaO%2TX+Aaj0fnDZY0+O6HAAN+2M3>twC5F@?h<Zn`$(;QYqx8Lq~0~tiOJ7lV7e&m'
    'u9F4e`^MLy8cTrh&7xYfBhZRei!si7k+RNyd(@Pmi<9{B)hHq39eg+%C1lFE=S=UEF%EL>ay+e8&6@KT'
    'T$G!kGDW{9->!61lfQwPLv{sbYaru?At^rjEW;!bu{SUdvJ7gpYSsp|SUmyZ(kF#LdNZGMLwhck{|59e'
    '*+ZA>TQ(*M;|F@h;04K1@*BG!{BvCx^ub`fuS&B0Y7V9N2wQV1KUT8O{%_!^TjMgjw5J)AifqD2F#Iez'
    '6!T?<@+dnvONzyW2^Lwi*nqgcUzZL4QLI+QJzTz=Har>YqTg2ahG;#Mg2d^IpF<^P%Rr*+6mXX(=P@z>'
    '$xxedk}1;)3<&TYJAVs?ln;X_KxFaE*PEqbu3~?`XM)?`-@h9AM+t#(Hv&$MN<}h&jFV<b6u^EuAk0$A'
    '064H~MRj<nuMG~F9f(w%`PrmwIAF$drKRc`&8ch&lSK6-C$l~iEm<knT(b4`U&`_N%II-z#7G$qIY6_W'
    '3h2>Iy&(!LDF_<zA;S2!oEQ1Wa@AC1FCCJB0ZNN8-QAFS_(-y#`AAkQ7-h}o=B8}cWK0#zJ!qG^ZmSs`'
    'mRK9T9xfD`05zNWk|C5Is|g(-IT;h{P2=!Soe<ZX3AKbbWF8gNBto_)0K%+##ZBoeXC#WI@l#kA<2ukd'
    '>L;8j^Wn#G9Re195Hh}~UxkdVA2-zsg}f$oKPchTmiNKoE~WL9&ev60G)&ABw~X=0)`)r8G(;j4V*6k~'
    '+z^h&^=e|5kU?KfEA9`q%LJoH6A<gFm_>lbgIZNl=O}cj62$(T94jZqx-eH-&n%qk<&>@fp5<PW1k1{D'
    'wz(k)BX`N>CCEy>T-8%*oy<bEQn3&U^1m2`C%w6EQ3+HFuvZ&yV(u;I>|K_P)9m@CHK~B<)CT%{|0-}5'
    'BB`c-G*!J|0K(SlAo|Oq6<SULOcG*d|61j#(WBPZWdmdCm#5OLwwo5KYdT_WPQ>RR0|LfZuci#DW1E4H'
    'VUSDC#<W^!#l?Up6F^iD0U1_^Z0J;*yZkfE-nru2<m`FjhP8tmt!oF5YMxkdX{(%uj`Vu0Pd?sdn2sz|'
    '4uO?BH0u0lzJVC#4GQn)!4jM<wu)z{dDNg`yM8)qdbqad>_`@bz5iJBlzzx(JK2T8K_F5umxGS}9ax>M'
    '+N9FSeqv=>_hjut2crR)Ig`*W_O~9<Y3)xIMnKE;6}3`;%5S_Z!)meh#g;XuC%cALkNjQaOdB(){nhrg'
    'Ix2T;RW@e09!z!b)F=ZtT$gnS2y~md(UR$+EWtP@2Ib7Q5<5=pz+vMFnF`d?#-jg-^Mw5QDrsZRJym&('
    'CwR3sdS=sC$Ffdj1s3-Tc`%JNPa)Vd!aDyszQj&!8Pu}OAr{H(?qp0;Sbz*VP3*I-K@EX0Nt*VmR$Q%3'
    '7*tru8$2$%2W^YAD#^kuXgFQ9r#kEOZV)ocf>oN3t(9uCfcVXxDIw!PKnJ78=fHW=VaD*Ww%hb<Ys9R)'
    'DaQA?9l!ej)Ep5G<ZZD>$n#^sKAwS?0u|g#O&1(hG2q4n(?vpU39zAnP%e~PXA=nP?0b9d!(z}~%MZ))'
    '@1^Dg@pyD|9m&m{ub^MpTq5&cD$8L7zFT_{<1*XKCmt?773=YBUN2_%X^d`5eU=y<!?Np9R^xFo1vDPp'
    'uP6mN&D`L3*f;68>G!AUHr!tiLl}NP$j~<Rv+U6eEgz{l5G7kUnC{;6oBD3Xe-|F^LBj5jz)z;HjXHP~'
    'QgX!lgDR97jem5bk7?llGj{95M+n6+z$2<KqObX_7AcEK{b_NtDkl7DOrQQI9N=WCEE^r2pjt~o8g^Y~'
    'x|z*re8jwB*E9I2VyhvnkHNpE!7J>y{c15O@7V8I@{HFqNM@xdCUqr;*_FK~(lU5Ks&9luE2jCLN7%~U'
    'UgY7%)x=jMMupSYB7|8s4g&xlPcdT5A2fJC4He2QvzjsS=?()uKeF2Zf$kKL!?8Bw+9RA^Zm|X%bBHTu'
    'Xdilbp<QRnZD8}4{qzj#(dA*2jDDO8tn1ntkvudoKB}n567+}io(0*E745slhXt8H35!e=<8VuVJdioi'
    'v*}BQ0Nc+Jizm`#7v;!|f1wLM)q2Rs<Z~As6VKj=V__0<$}pY}g><i>WL0Py<>a}GXRHHo_~_aPfa1XI'
    '4Y3YjSCaS=00L)(yjjpIvq_ca#R+;1h-?!1CxTOd8laaf#zk{W7oBoN+0a)ujqfZTI^Q%C78EGfl?4rI'
    'oft^^Be)Bp8=(VN0giFGiZo)irWGM&MD||1AAkI@%0HFW%`Fcl#Yivj+d#l_>jNnl1sVF2kyc7g3bZ>J'
    'h5VXqh@4+7H1{GIyZJHN@wTo(j+|4eVUkFgs5`6T6W!-j{l9sMV2D3_I0s)KI4smzfeMW<SPLB!K7<z-'
    'f(<+=$HhGjK_*-Gh6tas_7aZpk{nR)3G{SYu4v(}jp0w$ab5I<fp=G}U9@7oF2*0gSm0e0xUncMN9$N^'
    'PM$06BXPIZhGC0?7>)<~U%qqz)CQ!0zHouyexu-C95`^+j-WsfU6SPJLvN|5kq!b5EGQi({-qk>uugGf'
    'zyNI_d(E!{)8X98LQT#TWW>-lgY*|Up^?+hs__6L#*Q#`1c8Ss`Z^Rr;Op+_@E`_QOY>m=<;zYOe1yi{'
    'fR3PSU(*`Bc=@6Wp1wUn8uQziT~NVQjnUzYFS?+zJ%>u~pbHimX!rySUBb5)_E<F5QU3e?v1FF@tQy~s'
    ';?fPo5|!sic}50%T$lvgm>tQ)RzD$ijn~-Z!h@8MX^R6E%jN7ohga%5;ndTqkbsN{=9?5y5{N?Bp4meC'
    '2mHA&R8+0F7G!85rh}oM{_&50JRzq~*9qs-h-?o=KfQSOLj(z+F+mdG*Ya*W+e`>H@4aRO@=M6OL7?RV'
    'ie7W)(XNg}Ik?EZanChk3jcl&Qcm_vf>(Uw{#aBqIyQ2uyRyR=(2U=wW_ikyh^`^cC_D(;#WRWl+K-!p'
    'jCgYPL2z^#KxoPtuRnx|00|N{n_C&ZFM|K=mVg^k1A11>c&IM_LT%%gn7M7q;0>ulm+wX&7Xnfd0=Qd^'
    '>v~<xa#h6hySRc!!G!1~sB|0H*xU#xOUcaIKG<#^^O)v1c6y!=Gpk}Oo(5q~&<if0;+h6C_6Gq`Lw_)U'
    'l-kM^dyz4xb}J0$Nszyu;RlQ`ih^~ig|v#y3G)74xGJu6pR|c5*7b!;C3e4qUEIQ&i>z09qx<2q9<zt;'
    '#sfig>(}}SuYSB)k%QJcS5^mMps}qGo)uuvwqXbZmXqI##?RuJufX+PR)xqNMGYlroda6}e5D>4e`z47'
    '1-N_V09P!eeuRDWpeU=$C}fQB8)QZyJ58jGq#eXV4wz9q-#v%{gGnTuhj7rY5)J7g9x%W}!ufjBYp00='
    '^N?+|=5&HKra$c$wVNx;EI!$vyVH7K#2JB0n!?^Wks2f=3Z`tZhCRfTnhjiRuHoS3Rq8indJSynFO$pL'
    's-d?a$x=jS#-^AhH0Ruu90Kd4nU&-qHB87{B?3!?b~xWO>tt3IAIqd#C!2*>Hp<Dq@a-&T^*z^xaVf&j'
    '&rP{xfP|Q3VpmHE>aS9`h#JMybnAA4u*k#QP;d<KE;7jUL7szcKisX!=Xh^rZjI7eg?8>ya$u?%ps8Zp'
    'V79vDsv4K-a(nR7tIEB{aAdF*qcShJ39%y~i6&`TCQNyA1GR6`kryy=dat3|WPULUk+B_1`u=u5Wi4mc'
    'Np3#n9h=Z-grwOl>4<L<PhLZD;T70{h1y}BZe4S+k?r8uGTi|+OlQh_+DcFZX>!zs$n4DqS6NF9((x#%'
    '8MxvSys}w`ny8`LTFAuC>zP%HQabC@5Zp`p)YtV4;f15pszR90A<ts22m?7kxA9@=F`u&t9A*>JLr|pm'
    '0F>mk>v-=RvSCj|={$QhReM%S!Y>Y<fb@$4mu%k5>Sg&P#5pOmid9n`{^rrt85M+Z?k2Iqs?w*+u64Q<'
    'r0_(@QLeA)q<(ZGIT^E+<Qp5mPeGg!a>%n4`YFgWLaDx54(FPaNyVIqdNVYU$$au)vYBQqAdZJmtTGBd'
    'ur%p~*^8vt+06C{dT^|j3?4!G>czcr1k*BE$sPJ2qlyGw(p*EGg4t(K&e@jp$dtU?hyro<0Nl$6H-az4'
    ';xFi#u`X>iEypCJ`-Mr@iH2z-?Yc7|^%n;Qbu7xItpd~!3hOu&E!Z^56ACRDem^N{C8JbOH>ihZ&}Hha'
    '!El;-D5H*5lp=ShO6RI{t9r=dK3FfOt^>$J*YB=HX?vZuu5#fACDRhFfw;aQOtfAq!_bXT#S^T@*8uK3'
    '7V<GND#bXR!9A-3uZ*m8Zs?v7>4H|$iKxce0isx=UU6STtVF8TRpc&SbUF$m9qB#)4PsmlR!z**zi6-R'
    'A}A=Q>0P*waQ$NgFjBI3dR=JIRbd5L8#_kIW;a(?M~>{&zK|y!u+YjBh*c1Ww@^>{#nEwwV~L3Mbbs+h'
    '7Rf#{JE1#fM{$B+KU0!22B4J{y>UH?5teS*W#dbuL-EN8Ir*wpYQvb0=8bA2<r9q+QSlh@Sfnr!wRMyx'
    'F)7DYV<tlh!2|m#HPLqolmT%OY&YQ6=q~LOaWdV)Y7CpLsI+qcj3(lQ)$AINGYGiP2;3;>b8PKiBMhh8'
    '4r(JSlZ~}QT6$CjC9y?r&G$(jg0s(U3Sb87yppgt?r3E&Z?Ret*{vF&2{xs-hxqj(fU)(eb}&_M*1E2V'
    '=Edk-F_LJtx%eBMVC@7Kf?GEULs7+(TlPYI644+1$Mp<iTypQ0IRryUv3ohpfBo|pkCC9igM>VIS^W0i'
    'k{dsA^cjA^7%j_eBh?gJw3sMPGJ7E)ZI4nxjnM)G(Sz9`V)4I<RFQcfb(Xc_D|Gn5^(^ZU86_h7Jhj_{'
    '8?k~!hhv1GS{m+<=iY6^0_8pI8^Ah+u|dL3r)|~nrM#k6PN$`~>12Tzf6Hq8b~7r^u)6@A*Z%tHR5rd1'
    '!t{j)i%W{%)q_{{FkAVNW5pg?nw6x(E=Yo_fCv#~)23I9QS-AFkyX7kZD+pfsk5S|UWqZv^dAhsG7skb'
    '{&O=-W>v$oGF;K1-76H9U6Mv2Nt5lGnf3>--2&xL3>0amwT1>92u|6NZ$y~s7f{ys3!=v^CVluigSh`K'
    'Sg~`?;lh%1-25>yFNy_fACR5-{Nm(j>uclw!Suo6F|(y=8kM#Dl>Z7BuQ}u-@Z+Rvu<}#M7Jbqs^y-cM'
    'KnKdK2aQSTh|7@%xFK*@_pm$RuJpLdM=z9nq>;g!*d78$$$@8Cuy4#8j9K&5Y)7CvmLeK;H1I?YP_vP7'
    '<76zC=A~@-G46UTjJ?IL*h|JoSgWjC;3LN&2M+0mQ*DI0+6MLwWZjOD&xdAMZyX2MDBv7xIZB8gU}3{r'
    '6u8^Q&~K2g;kg;wv><&pja5Je#?dIK6t&qp=JiKuP6F5HJw|&e6;zpUY;@v@mly+@%p78n;5lcg6By>T'
    'D9EmDxo9z=@A+8^TTD!Ws31VBj^`LZAR7XLGQv=dXC5M~I3r>FF}D(MIse(HwQWWSpGgQ=Njg!a>zQvF'
    'IFrPaz$7+JgwhYaHOA2lk=upEG$3CT>1zY2t6Jdp4bTlD9-X9HLDwfL+Y|%qN^a}<(y|1a=hadBk=Dd_'
    '4lARWvms6c9`Q$!rU0Chx<uT`Cx*YoIqK-t^Ss+Riu9_Xboe#nZWbSS**MmJmGyQ}a-aT5NAGmo;X%i('
    '*U0#g1Pq5z7x2Emxb^D`AlRGg4n7KM_k=I!1+6cTu#akg8?n4Ru*c2#o<}37Pm^`ssioG28TZVjxO5%q'
    'sG$EuN&|n~iG!g*N3nf`3@E*%;Y83b3DFU9A4T+yOU1sm-rgU+H$2-e(p5|y*DtT(kUo{x2hCZd8%dpS'
    '7L4I|Z193X%e6?J{qQPlqXwS8XuBh*l31qju42kmtk|24{CL0&Xi=50w2e^}5J+{_4qU8N0umV{uL9H!'
    '0YS@MIi**0Oe8bu7NQ+K!<0Nl(@N8*jx#N-u(LaY2S1$Tjj=S{tdD@Is<6|^MMd#i{AS_P%ihRFFqHoI'
    'S>TS^s3Qp8Sks@h(==}(Os)M+?|o(FWedp6!EJ485UH5*Jibpj+T)f|$JKXW=NN69iO*Y@$230kTpPh@'
    'Fuh+}{?AC$S{8ACyRugCz;r!I4$J4iwO?mxljc#|P<UpNjmRS)__p7cR2f~J26;4OPdCXe#3A8j2-$xe'
    'WPS^=6BBaT>G|!<!814z$<N6M57_dU+qp~<E)%xMJeR4M;}rldaD<c;Yn%;#XY)OqoZ^=6$ajNx4_gO&'
    'D*a_Qbhuz**L~^r+vsH%xp5n3!2&l-RlV$hw8b}heIR?ruA-W7ce&@nv1zjMR2oA}HZ8)<!k>|WFCW>j'
    '(M(wKa4|>)ICA}BOlO)CYb2)?Au&z=`(OY4Ka%s4i$6Sn{pwxP-#S~uLuTVIi&Zga4;ZElg8b%U*}17%'
    'yQ&-d7+Gt(1l%qSa+eA+8sW8yhorbB)RL{01@&Q)(3&Rv(%2xm-mKX)PN<tR?P16F&j!h7`+un`{{(%='
    '621yF^w_?&MvrwC&%9!GHyPL>ma`n((Q~1B3l*G|`4!1=&ffYY^CS&755c#ZNQZ{`#l&%j2{SlH`uQI{'
    'P52_dXqg}X{EvKooW1?oPLKA6JlVWpwiIkW`~++7&uV7q2C;F`*3uLwapxm(*GdAJD{yOi^lZ?<O5yqN'
    'FaP#m|M^eJo8#v!=Iv)YCt{itF)OaiS^Wu_0*d_q<pKSaqtO^w^l;g#%wr0?If~!&{=M>-tIh;4dwVvN'
    'kna_YbPyv(ydt$b!^Qwb7`Dni3|mkb!(2&kj5)yFwL^Fa7#M}$^TIo~kbBLwY0RtoYnwwRUyTOr>~EG6'
    'A~Gc3j)*wnciCmW&yOYP8<2}rRc~eKL~{#Wmy7W&9cSozkhaVbk;h{QBhriqxED7f#Ry{=%pm}!V>MN|'
    '{siD;IHsl|K0sPB@KTeKUmzu#%wq`hcvxj37st@a^Z}nHXIV9dFx1g5MQ$4m!x66z1s!ApX?TJS;Unfe'
    '9mdc|vSF6<j21&He9(m9X(@(Q!V3U{p1cSG`aOWBb!48dwp~3=y`D7uy)|d(di`oRzP;wBA#WB=0_&*O'
    'u!R;<R%k;S6NP}23=PJ}poB1CJbWRI2vrEvbpsmGsL+KlRV>6Ijf-Un6Gnp`(zY0fFik5AA`QnqglQRp'
    '5vkXSj1%EyE#q5;`FV!6EDlq#gZYL@m)ojo>y6kpf-<!5eLA<)lQiPuYdm!U-$LITj2K^$jTqe`^<rLR'
    'MFKvWzPy|(A1REi#zc#*&3ch8T6h4fwJsxE!B+_q)4;Vvm5>rw*<gCyRA%|cGc!VUqxh6xZ|>R4onq7H'
    'n5Gg5QLH5I?FfeYso+he6o6ZrtwONALlj)$R+xA(BtZ8)B7-KcR}-%JFm=RAc8*~<Crz1V9XWr(lvIj2'
    'BTv<Pj(vsM14YIw-7K8~%Y*h{hCYV!xc98XsCWgDS`^&W+S{JpB-6R$$Rrh+h1ek}lm{Juo-`)TLrnL~'
    '_l#)^y1O{x@0qrdG2yQ~Z<WS{GlbtMjiHfynx&pq?Gizgq5SA)+arQ&Y&N)8*o!O5+uvMm>lGX0D(3Cg'
    'nD7}B=c@Hh)~;bb5f8O?kMfCrzqN(55Amw?vHBLmHo}$fb@~=M0`4mO_-va1JzKp0f%djec5$vk539G5'
    'Q?OUz2h&>!8PKlGKwOD`vO_3`LLgQuKrw(dz;`X!5RZ9_88zAS)X=&Kws&qtq!5NV5t>P)1&uDFwlxi!'
    '%B0#xpRBi4IH)Coy_T|u?Y**<J@gUq)Y{Ye^V{%?uI`t~@m)FIP!T2SRTEN6vzeEZ<oZ6LHxg;m{9>Lg'
    'iIz%lagZ99b+ZrbeAT>KbCpTH6GS}$DqFTlDPuh)lwral`@|=PY++{u>t&+>q0j8CgDj|&njZ!|R0#vK'
    ')qWTPzArQc*+$_QLEaG>Vz2KEY%Se>0%w@Cyk(I($w~>1^9|O+P0FdvTHm-5P?Ny;+nED$mC|!Td;pNx'
    'j3e;aXGd!X0WSo_`DPpptc3UJ&`pBx!aE7>HJVd|VB1fL0c>rcN8b!FN$3gUqRX-EL6u9SC$+y1O`bEt'
    '`TL>o#D$zx#m%B_*3}qt=QtcOCuHCRX#5>K`dMFM&U|<QoEyrBs=O1P=O$X-)wRPkg#F0+0&8}%<5R0X'
    'OqOrM>J-X!qUyZ$4o#glnUVy_>3dc<C5;84TNZNlI<>BvQ!wA<^Z!j302)H+CBRA{x|NS#`utGmlbL{D'
    'fg;W&+k+K$YMGv_kZa3wX9XQxQ4a1DIPJVHJm~?$U3c0)=5wdn9<;Di4LHZVuC<^8t%HLN!tLs2gWfg2'
    'v(5Cdg<Ni?pDpBg^Jm8<SRhr49|gA5>qd;smo4f<VQ_M2l59c7D8te5WY}DhM>1XDMEser1tTs%G@(we'
    '$e1kCkzUEj1kCXc$}Sn5JNhc)63srsZ5da=qdl12QZsjUW_Hc9yn|n}a}wlTT$~*e9C!0}Mkg%p;`nSy'
    'L)*m%+A@W@yF)vrAnxiJ?UFY55l+%>iGm;HFKt=!AMHA|?OP@lRk};-pTM`;H3xDxH>({w3TZs5<vaOd'
    '1NPmWnAz1)8%5kSq_%+!T6}GJXV;pC&f}bI9-_Ou-+Tn0%psQs{8c-+=tATk?X>d{f2<oXjd0m@b?l`<'
    'S6;lU&qIg5;UJbRhS<WUY`+yH?jwUP7qP5t*0=SFdUhVoji%IrA#T23GD_vgYEn|em2KsXVkktZ14B>m'
    'R`pc$l#bR=Tb|JM>g&Uw&>8D%*^{_K(+H1XSHEGpgHP}=p2UaV{{Wx~f8_'
)


def test_defaults_keep_live_layers_and_binding_does_not_claim_effective_config():
    # The current observation change is not a historical source attestation.
    expected_current = {
        "SOXL": ("9675f56e9ea0d4be36751b52ec22951ddc29e2546e305a1e5b1aab5c6d60baf4", False),
        "TQQQ": ("90e8ded53a2e4389f6060d88cfd232046d90e6775dda47379e8d06415b2cbf42", True),
    }
    historical = {
        "SOXL": "4831c9ab84f2373579e2cf118e880e13f369cfdf14141db0bf06424508f1c4ef",
        "TQQQ": "90e8ded53a2e4389f6060d88cfd232046d90e6775dda47379e8d06415b2cbf42",
    }
    from us_equity_strategies.research.rs04_b3_live import EFFECTIVE_STRATEGY_SOURCE_SHA256
    assert EFFECTIVE_STRATEGY_SOURCE_SHA256 == historical
    for profile in ("SOXL", "TQQQ"):
        config = default_config(profile)
        assert config["income_layer_enabled"] and config["market_regime_control_enabled"]
        report = binding_report(profile)
        assert report["effective_config_verified"] is False
        expected_sha, expected_verified = expected_current[profile]
        assert report["repo_strategy_source_sha256"] == expected_sha
        assert report["strategy_source_verified"] is expected_verified
        assert report["strategy_evidence_revision"] == "4a3943883cd6"
        assert report["strategy_evidence_date"] == "2026-10-11"
        assert report["current_live_readback_performed"] is False
    assert default_config("SOXL")["trend_ma_window"] == 140
    assert default_config("SOXL")["blend_gate_soxl_weight"] == .70
    assert default_config("TQQQ")["dual_drive_crisis_defense_enabled"]


def test_historical_soxl_source_fixture_keeps_original_positive_binding(monkeypatch):
    from us_equity_strategies.research import rs04_b3_live
    from us_equity_strategies.strategies import soxl_soxx_trend_income

    historical = zlib.decompress(base64.b85decode(_SOXL_4A394388_SOURCE_ZLIB_B85))
    assert len(historical) == 51311
    assert hashlib.sha256(historical).hexdigest() == (
        "4831c9ab84f2373579e2cf118e880e13f369cfdf14141db0bf06424508f1c4ef"
    )
    header = b"blob " + str(len(historical)).encode() + b"\0"
    assert hashlib.sha1(header + historical).hexdigest() == (
        "cebea7508ed237c6c1ef90cdd2db49159e2011f8"
    )
    source_path = rs04_b3_live.Path(soxl_soxx_trend_income.__file__).resolve()
    original_read = rs04_b3_live.Path.read_bytes
    # The positive case uses exact old bytes. A changed old fixture must reject.
    for payload, expected_verified in (
        (historical, True),
        (historical + b"\n# SYNTHETIC_HISTORICAL_SOURCE_MISMATCH\n", False),
    ):
        def fixture_read(path):
            return payload if path.resolve() == source_path else original_read(path)

        with monkeypatch.context() as patch:
            patch.setattr(rs04_b3_live.Path, "read_bytes", fixture_read)
            report = binding_report("SOXL")
        assert report["repo_strategy_source_sha256"] == hashlib.sha256(payload).hexdigest()
        assert report["strategy_source_verified"] is expected_verified
        assert report["effective_config_verified"] is False
        assert report["strategy_evidence_revision"] == "4a3943883cd6"
        assert report["strategy_evidence_date"] == "2026-10-11"
        assert report["current_live_readback_performed"] is False
    assert hashlib.sha256(original_read(source_path)).hexdigest() == (
        "9675f56e9ea0d4be36751b52ec22951ddc29e2546e305a1e5b1aab5c6d60baf4"
    )


def test_binding_report_rejects_synthetic_source_changes(monkeypatch):
    from us_equity_strategies.research import rs04_b3_live
    original_read = rs04_b3_live.Path.read_bytes

    def changed_source(path):
        payload = original_read(path)
        if path.name in ("soxl_soxx_trend_income.py", "tqqq_growth_income.py"):
            return payload + b"\n# SYNTHETIC_SOURCE_MISMATCH\n"
        return payload

    monkeypatch.setattr(rs04_b3_live.Path, "read_bytes", changed_source)
    for profile in ("SOXL", "TQQQ"):
        report = binding_report(profile)
        assert report["strategy_source_verified"] is False
        assert report["effective_config_verified"] is False
        assert report["current_live_readback_performed"] is False



@pytest.mark.parametrize("signal_price", [120., 107., 97.])
def test_soxl_adapter_calls_original_rule_with_own_equity_and_income_layer(signal_price):
    from us_equity_strategies.strategies.soxl_soxx_trend_income import build_rebalance_plan
    from us_equity_strategies.research.kelly_core_return_generator import _filtered_kwargs
    rows = synthetic_sessions(2)
    indicators = {s.lower(): {"price": 120., "ma_trend": 100., "ma20": 110.,
                             "ma20_slope": 1., "rsi14": 50., "bollinger_upper": 150.,
                             "realized_volatility": .1} for s in ("SOXL", "SOXX")}
    indicators["soxx"]["price"] = signal_price
    row = replace(rows[0], live_indicators=indicators, context={})
    adapter = LiveRuleAdapter("SOXL", default_config("SOXL"))
    for nav in (100_000., 500_000.):
        book = {"cash": nav, "quantity": {}, "nav": nav, "receivables": 0.}
        account = {"available_cash": nav, "market_values": {}, "quantities": {},
                   "sellable_quantities": {}, "total_strategy_equity": nav,
                   "cash_sweep_symbol": "BOXX", "metadata": {}}
        kwargs = _filtered_kwargs(build_rebalance_plan, adapter.config,
                                  ("indicators", "account_state", "translator"))
        expected = build_rebalance_plan(indicators, account, translator=lambda k, **kw: k, **kwargs)
        actual = adapter((row,), book)
        assert actual == pytest.approx({s: v/nav for s, v in expected["targets"].items()})
        assert bool(sum(actual.get(s, 0.) for s in ("SCHD", "DGRO", "SGOV", "SPYI", "QQQI"))) == (nav > 150_000)


def test_tqqq_adapter_ma200_and_recorded_decision_comparison():
    rows = [replace(r, signal_closes={"QQQ": 100+i*.1}, context={})
            for i, r in enumerate(synthetic_sessions(300))]
    adapter = LiveRuleAdapter("TQQQ", default_config("TQQQ"))
    book = {"cash": 100_000., "quantity": {}, "nav": 100_000., "receivables": 0.}
    targets = adapter(rows, book)
    assert targets["TQQQ"] > 0
    record = {"session": rows[-1].session, "history": rows, "book": book, "targets": targets}
    assert compare_recorded_decisions(adapter, [record])["status"] == "MATCH"
    bad = dict(record, targets={"TQQQ": .123})
    assert compare_recorded_decisions(adapter, [bad])["status"] == "MISMATCH"
    assert compare_recorded_decisions(adapter, [])["status"] == "UNAVAILABLE"


def test_adapter_never_fills_missing_history_with_zero_or_silently_clips_cap():
    adapter = LiveRuleAdapter("TQQQ", default_config("TQQQ"))
    with pytest.raises(ValueError, match="LIVE_SIGNAL_HISTORY_MISSING"):
        adapter([replace(r, context={}) for r in synthetic_sessions(300)],
                {"cash": 100000., "quantity": {}, "nav": 100000.})
    with pytest.raises(ValueError, match="BASELINE_CAP_BREACH"):
        replay(synthetic_sessions(3), lambda h, b: {"SOXL": .7},
               profile="SOXL", mode="baseline", kelly_full=0.)


def test_de_risk_only_blocks_mr_without_changing_default_b3():
    from us_equity_strategies.research.rs04_b3 import Features
    f = Features(date(2020, 1, 1), date(2020, 1, 1), 100., 100., 0., 15., .3, 5., 2., 100.)
    state = State(regime="RANGE")
    kw = dict(decision=date(2020, 1, 2), index=1, profile="SOXL", baseline={"SOXL": .6}, held={})
    assert advance(state, f, **kw).targets["SOXL"] > 0
    out = advance(state, f, allow_mr=False, **kw)
    assert out.targets["SOXL"] == 0 and out.sleeve != "MR"
    result = replay(synthetic_sessions(400), lambda h, b: {"SOXL": .6},
                    profile="SOXL", mode="de_risk_only", kelly_full=4.)
    assert result.metrics["closed_mr_episodes"] == 0


def test_pseudo_holdout_is_archive_trailing_24_months_and_excluded_from_wfa():
    rows = synthetic_sessions(1800)
    plan = historical_plan([r.session for r in rows], feature_warmup=280)
    assert plan["pseudo_holdout"]["weaker_evidence"]
    assert plan["pseudo_holdout"]["previously_seen_development"]
    assert plan["pseudo_holdout"]["end"] == rows[-1].session.isoformat()
    boundary = plan["pseudo_holdout"]["start_index"]
    assert all(f["test_end"] <= boundary for f in plan["walk_forward"]["folds"])
    assert plan["forward_holdout"]["start"] == "2026-11-01"


def test_evaluation_keeps_continuous_ledger_and_never_promotes_weak_evidence():
    rows = synthetic_sessions(500)
    def rule(h, b):
        return {"TQQQ": .45, "QQQM": .45, "BOXX": .08}
    paths = {m: replay(rows, rule, profile="TQQQ", mode=m, kelly_full=4.)
             for m in ("baseline", "sizing_control", "b3", "de_risk_only")}
    report = evaluate_paths(paths, start=100, end=400)
    assert set(report["paths"]) == set(paths)
    assert report["paths"]["b3"]["observations"] == 300
    assert "giveback" in report["paths"]["b3"]
    assert report["paths"]["b3"]["deflated_sharpe"] is None
    assert report["paths"]["b3"]["regime_stats"]
    assert report["recommend_shadow"] is False


def test_windows_and_stress_use_same_continuous_book_and_fixed_dates():
    from us_equity_strategies.research.rs04_b3_evaluation import evaluate_windows
    rows = synthetic_sessions(1000)
    def rule(h, b):
        return {"TQQQ": .45}
    paths = {m: replay(rows, rule, profile="TQQQ", mode=m, kelly_full=4., cost_multiplier=2.)
             for m in ("baseline", "b3", "de_risk_only")}
    windows = evaluate_windows(paths, feature_warmup=280, cost_multiplier=2.)
    ph = windows["pseudo_holdout"]
    assert ph["status"] == "COVERED" and ph["results"]["continuous_ledger"]
    assert ph["results"]["paths"]["baseline"]["observations"] == ph["end_index"]-ph["start_index"]
    assert ph["results"]["numeric_acceptance"]["b3"]["giveback_required_reduction"] == .1
    assert windows["walk_forward"]["status"] == "INSUFFICIENT_COVERAGE"
    assert windows["forward_holdout"]["opened"] is False
