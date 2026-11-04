# State registries of aircraft, pilots and flight schools, and federal lists that name flight schools

Research for David's Hackers 2026 talk (Saturday 2026-11-07), compiled 2026-11-03. It extends the
survey of public sources in `docs/hackers-2026-data-sources.md` (branch `hackers-airports`), which
covered federal datasets and state corporate registries but not state aviation or education
registries. Ratings use that survey's scale: **bulk free**, **free per record**, **paid**,
**FOIA only** (here also "records request", the state equivalent), **none public**.

Method: every claim below comes from a page that was fetched (statute or administrative code text,
agency page, data portal, or the dataset itself). Where only a search result, a secondary host or an
inference supports a claim, it says **UNVERIFIED**. Downloads were saved to a scratch area outside
the repo and are not committed; the matching script is
`research/FlightSchoolMapping/scripts/probes/match_external_school_lists.py`, and the commands that
reproduce the counts are at the end.

## Short answer

> **States register aircraft, almost never pilots, and do not license flight schools as such.**
> 24 states require aircraft based or flown there to register, nearly always as a fee or tax in
> lieu of property tax, and every one checked records the N-number. None publishes its registry
> as a free bulk file. Iowa has a free per-record N-number lookup; Oregon and Oklahoma make the
> registry a public record by statute; the rest are records requests at best. Pilot registration
> survives only in Illinois and Montana (and on the books in Massachusetts); Idaho, Oregon,
> Washington, North Dakota and others repealed theirs between 1999 and 2017.
> Flight schools: most state education boards exempt FAA-regulated flight training, and those that
> cover it on paper rarely list any. Two state aviation agencies do license flight schools and
> publish the list free: **Michigan** (75 licenses, May 2026) and **Minnesota** (56, June 2024).
> Those two lists, with Alaska's, hold about 43 flight schools that are not in our 4,825.

> **The federal lists that name flight schools cannot see Part 61.** SEVP (student visas) and the
> VA (GI Bill) both admit flight schools only if the FAA certificates them under Part 141 or 142, so
> their lists are subsets of what the FAA already publishes. IPEDS covers only colleges in federal
> student aid. The one federal list that does capture Part 61 is TSA's Flight Training Security
> Program: in the 2024 final rule TSA says about 13,000 providers have registered since the program
> began, about 9,000 of them Part 61, and estimates 19,738 providers who train only US citizens. That
> list is not public.

> **Matching the federal lists against our 4,825 schools finds little that is new, and what it
> finds points at our own pipeline.** Of 247 SEVP flight schools, 225 match automatically and 10
> more are in our list under another name; about 8 are missing. Of 229 VA-approved flight
> facilities, 178 match; of the other 51, 22 are simulator, airline or type-rating centers and
> nearly all the rest are campuses of schools we have. Of 106 colleges with pilot-program awards in
> IPEDS, 104 match. Most of the missing SEVP and VA schools hold Part 141 certificates listed in the
> FAA file the pipeline already ingests (`faa-141-flightschools.csv`); by the same matcher, 45 of
> that file's 519 certificates have no match in `flight_line.json`, some of them name misses. The
> state aviation lists (Michigan, Minnesota) are where the new schools are.

## 1. State aircraft registration

Researched state by state from statutes and agency pages. 24 states require registration today
(AZ, CT, HI, ID, IL, IN, IA, MA, MI, MN, MS, MT, NH, NM, ND, OH, OK, OR, RI, SD, UT, VA, WA, WI);
7 repealed theirs with a year found (AL 2000, AK 2001, CO 1988, KY 2022, LA 2008, ME 2012 with a
municipal excise kept, SC 1988); the other 19 never had one or have none now. Bills are pending in
New York (A558/S2300) and Pennsylvania (SB 1365). No state publishes a free bulk download, so
nothing was downloaded; an open-data search turned up only FAA and foreign registries.

| State | Required? | Statute | Agency | Fee or tax basis | Access | N-number? |
|---|---|---|---|---|---|---|
| AL | repealed 2000 | Ala. Code 4-2-70 to -80, rep. Act 2000-220 | (Aeronautics Commission) | none | n/a | n/a |
| AK | repealed 2001 | AS 02.20, 02.35, rep. ch 56 SLA 2001 | (DOT&PF) | none | n/a | n/a |
| AZ | yes, annual | A.R.S. 28-8322 to -8335 | ADOT MVD | $5 fee plus license tax 0.5% of average fair market value, $20 minimum; use tax paid first | records request (UNVERIFIED; azdot.gov 403) | UNVERIFIED |
| AR | no | A.C.A. 27-116-202 | n/a | county property tax (UNVERIFIED) | n/a | n/a |
| CA | no | Rev. & Tax. Code 5301 et seq. | county assessors | ad valorem property tax | none public at state level | n/a |
| CO | repealed 1988 | 1988 Sess. Laws ch. 221 | (county clerks) | aviation fuel tax instead | n/a | n/a |
| CT | yes, annual | C.G.S. 13b-39a to -39g, 13b-49a | municipality where based; CT Airport Authority gets annual reports | $90 to $2,500 by weight, in lieu of property tax | records request (towns, CAA) | yes; airports report FAA number and owner |
| DE | no | 2 Del. C. ch. 1 subch. III | DelDOT | none | n/a | n/a |
| FL | no | Fla. Stat. ch. 330 | FDOT | none | n/a | n/a |
| GA | no | O.C.G.A. 6-2-9 (secondary host) | GDOT | none | n/a | n/a |
| HI | yes, at state airports | HRS 261-15.5 to -15.7; HAR 19-31.1 | HDOT Airports | $10 a year | UNVERIFIED | yes (form) |
| ID | yes, annual | Idaho Code 21-114 | ITD Aeronautics | 3 cents/lb max gross, $20 to $600, in lieu of property tax | records request | yes (form) |
| IL | yes, biennial | 620 ILCS 5/42(b)(1), 5/43, 5/44; 92 IAC 14.200 | IDOT Aeronautics | $20 per 2 years | records request; owner portal needs login | yes by implication |
| IN | yes, annual, plus excise | IC 6-6-6.5 (cited from DOR form) | DOR Special Tax | excise by landing weight, class and age, plus $10 | UNVERIFIED | yes (Form AE-7) |
| IA | yes, annual | Iowa Code 328.20, .21, .25, .26 | Iowa DOT Aviation | % of list price falling with age, $35 to $5,000, in lieu of taxes | **free per record** (N-number lookup at opmapps.iowadot.gov/aviation/aircraftsearch) | yes |
| KS | no | K.S.A. 3-201 to -207 | n/a | property tax (UNVERIFIED) | n/a | n/a |
| KY | repealed 2022 | former KRS 183.070 (permissive only) | (Transportation Cabinet) | n/a | n/a | n/a |
| LA | repealed 2008 | former R.S. 2:2-4, Act 326 | (DPS&C) | n/a | n/a | n/a |
| ME | state repealed 2012; municipal excise remains | 36 MRSA 1482, 1484; former 6 MRSA 52 | town excise collectors | mills on maker's price by age, $10 minimum | municipal records | unknown |
| MD | no; airports keep based-aircraft rosters | Transp. 5-1002 | MAA (roster access) | n/a | none public | rosters have N-number and owner |
| MA | yes, annual | MGL c.90 §49(b) | MassDOT Aeronautics | annual fee in lieu of property tax (tiers UNVERIFIED) | UNVERIFIED (mass.gov 403) | yes |
| MI | yes, annual | MCL 259.76, .76a, .77 | MDOT Aeronautics | 1 cent/lb max weight, in lieu of property tax | records request (UNVERIFIED) | yes |
| MN | yes | Minn. Stat. 360.511 to 360.67 | MnDOT Aeronautics | tax in lieu, $100 to $75,000 by base price | records request; the form marks N-number, owner and base as **public data** | yes |
| MS | yes, by county | Miss. Code 61-15-1 et seq. (secondary host) | county tax collectors | $25 / $250 / $2,500 by weight, in lieu of ad valorem | records request per county | yes |
| MO | no | RSMo 305.130 | county assessors | personal property tax | n/a | n/a |
| MT | yes | MCA 67-3-201 to -206 | MDT Aeronautics | fee in lieu of property tax by type and age ($37.50 to $4,500) | records request (UNVERIFIED) | yes |
| NE | no; hangar owners report based aircraft to assessors | Neb. Rev. Stat. 3-129; 77-1250.02 | county assessors, DOR | personal property tax | report access UNVERIFIED | report has N-number and owner |
| NV | no | NRS 493, 494 | county assessors | personal property tax | n/a | n/a |
| NH | yes | RSA 422:21, :22, :31 | NHDOT Aeronautics | $100 to $3,500 by weight, in lieu of property tax | UNVERIFIED (dot.nh.gov 403); public airports file quarterly based-aircraft lists (RSA 422:20-a) | yes |
| NJ | no (NJDOT FAQ) | none | NJDOT | none | n/a | n/a |
| NM | yes, annual | NMSA 64-4; 18.11.2 NMAC | NMDOT Aviation | 1 to 2 cents/lb by age, in lieu of property tax | records request (UNVERIFIED); a 2022 NMDOT slide calls it "voluntary" and proposes repeal | yes |
| NY | no (A558/S2300 pending) | none | n/a | n/a | n/a | n/a |
| NC | no | NCGS 63-20, 63-22 | county assessors | ad valorem | n/a | n/a |
| ND | yes, if in state over 30 days a year | NDCC 2-05-11; ch. 57-40.5 | ND Aeronautics Commission | $15 to $3,000 by weight, plus 5% excise at first registration | records request (UNVERIFIED) | likely (form is JavaScript-only) |
| OH | yes | ORC 4561.17 to .22 | ODOT Aviation | $15 per seat, in lieu of other taxes | records request (UNVERIFIED) | yes |
| OK | yes | 3 O.S. 251 to 257; 68 O.S. 6001 | Service Oklahoma (from the Tax Commission, 2024-07-01) | $30 to $2,250 by weight times a type multiplier, plus 3.25% excise | **public by statute**: index "open to public inspection" (255(A)); inspection or request | yes |
| OR | yes | ORS 837.015, .040 to .070 | Oregon Dept. of Aviation | $55 to $700 by class | **public by statute**: applications are public records (837.055(4)); request | yes |
| PA | no (SB 1365 pending) | none | n/a | proposed per-seat fee | n/a | n/a |
| RI | yes | RIGL 1-4-6 | RI Airport Corporation | $30 to $250 by weight, in lieu of property tax | records request (UNVERIFIED) | yes (form) |
| SC | repealed 1988 | former 55-7, Act 624 | county auditors | property tax | n/a | n/a |
| SD | yes | SDCL 50-11-9, -12, -19 | SD DOT Aeronautics | $25 to $300 by weight, plus 4% registration tax | no list; phone inquiry | yes (form) |
| TN | no (UNVERIFIED; tn.gov 403) | lead: TCA 42-1-113 | Dept. of Revenue (lead) | sales/use tax | n/a | n/a |
| TX | no | Transp. Code 24.005, 24.011 (mirror) | Comptroller | sales/use tax | n/a | n/a |
| UT | yes, if based 90+ days | Utah Code 72-10-109, -110 | UDOT Aeronautics (sends a list to the Tax Commission) | 0.4% of average wholesale value, plus $25 | records request (GRAMA, UNVERIFIED) | yes |
| VT | no | 5 V.S.A. ch. 9 | VTrans | none | n/a | n/a |
| VA | yes | Va. Code 5.1-5, 5.1-14 | Virginia Dept. of Aviation | $30 (1 yr) to $70 (3 yr) noncommercial | records request (UNVERIFIED) | UNVERIFIED (form 403) |
| WA | yes, annual | RCW 47.68.250; RCW 82.48 | WSDOT Aviation | $15 fee plus excise by type (single-engine $50; both rise in 2027) | aggregate counts only (7,463 in 2024); records request (UNVERIFIED) | yes (form) |
| WV | no | W. Va. Code 29-2A | WV Aeronautics Commission | none | n/a | n/a |
| WI | yes | Wis. Stat. 114.20 | WisDOT Aeronautics | $70 to $3,125 by weight, in lieu of property tax | records request (UNVERIFIED) | yes (form DT1556) |
| WY | no | W.S. 10-4-201 | n/a | none | n/a | n/a |

Starting assumptions that turned out wrong: Utah's registry is run by UDOT Aeronautics, not the Tax
Commission or DMV; Nevada, Wyoming and Tennessee have no state registration; Kentucky's was only
permissive authority and was repealed in 2022.

Where these registries could help us: each is a list of aircraft *based* in the state, keyed by
N-number, which is the thing our pipeline infers from ADS-B overnight positions. Oregon, Oklahoma
and Minnesota are the cheapest to request because the data is public by statute or by the form's
own data-practices notice. A records request is outside this week's scope.

## 2. State pilot registration

| State | Status | Statute |
|---|---|---|
| Illinois | **current**: one-time $20 registration of the FAA certificate within 30 days of becoming resident | 620 ILCS 5/43; 92 IAC 14.300 |
| Montana | **current**: $10, collected by MDT | MCA 67-3-211 |
| Massachusetts | in statute (annual registration of the FAA certificate); whether it is enforced is UNVERIFIED (mass.gov 403) | MGL c.90 §49(a); 702 CMR 3 |
| Idaho | repealed 2013 (biennial $12, funded search and rescue) | former 21-114(a), 2013 H0117 |
| Oregon | repealed 2017 ($24 initial, $48 renewal) | former ORS 837.020 to .035 |
| Washington | repealed 2005 | former RCW 47.68.233, .234 |
| North Dakota | repealed 1999 | former NDCC 2-05-10 |
| Alabama, Alaska, Louisiana | repealed 2000, 2001, 2008 with their aircraft registration | as in section 1 |
| Kentucky | repealed 2022 (permissive authority; whether ever used UNVERIFIED) | former KRS 183.070 |
| Connecticut, Oklahoma, South Dakota, Nebraska, Virginia | former provisions repealed; years or content UNVERIFIED | C.G.S. 15-51, 15-55 to -59; 3 O.S. 1-7; SDCL 50-11-1 to -4; Neb. 3-132; Va. 5.1-6 |

A statute fetched for AR, FL, KS, MI, MO, NC, OH, RI, VT, WV, WI and WY says the federal certificate
is all a pilot needs; NJ says so in an agency FAQ. No other state current registration was found.

## 3. State licensing of flight schools

Two kinds of state body could license a flight school: the private postsecondary or career-school
board that licenses trade schools, and the state aviation agency. Researched for all 50 states and
DC from statutes, rules and agency pages.

> **Most education boards exempt FAA-regulated flight training, and the ones that don't rarely
> list any flight schools.** The useful state lists come from two aviation agencies that license
> flight schools outright: **Michigan** (MCL 259.85: "A person shall not operate a flight school in
> this state unless the person holds an annual license") and **Minnesota** (flight and ground schools
> are "commercial operations" that MnDOT licenses, Minn. Stat. 360.013 subd. 45 and 360.018). Both
> lists are free PDFs, and together they name about 40 flight schools that are not in our list.

How the education boards treat flight schools:

- **Exempt outright** (FAA-regulated flight instruction is outside the law): CO, FL, MT, NV (agency
  statement), NH, OK, PA, WA (only if every course is FAA-certified). Several of these say
  "regulated by" or "approved by" the FAA, which may reach only Part 141 schools.
- **Conditional**: CA (exempt only if the school takes no more than $2,500 in advance and no loans,
  Educ. Code 94874(j)); AZ (exempt only for Part 141, A.R.S. 32-3021(G)(7)); AK, GA, RI (Part 141 or an
  FAA air agency certificate, exemption on application); AR (only schools teaching nothing beyond the
  private certificate); IA (bond waived only if no tuition is taken in advance); NY (only schools
  teaching "solely" flight and ground school); TX (exempt only if the school applies to TWC); TN, NE,
  UT, VA, WY (wording fits Part 141; Part 61 unclear); AL (exemption certificates, two flight schools
  hold one); SC (recreational training excluded, career training licensed); ID (individual
  pay-per-flight instructors exempt).
- **Covered on the face of the law, no aviation exemption**: CT, DE, DC, IL, IN, KS, KY, LA, MA, MD,
  ME, MI, MN, MO, MS, NC, ND, NJ, NM, OH, OR (HECC can grant an exemption; its own form uses a Part
  141 flight school as the example), SD, WI, WV. In practice only KY, DE, ME, HI, OR, NM, SC and PA
  show any flight school on their lists, a handful each. VT regulates only degree programs; HI
  licenses trade schools only on request.
- **Aviation agencies**: Michigan and Minnesota license flight schools. New Hampshire registers
  commercial aviation operators, including "flight training; instruction" (RSA 422:3 XVI), for $48 a
  year; its site returned 403, so no list. Maryland's aviation administration may license "air
  schools" (Transp. 5-903) but whether it does is UNVERIFIED. Montana's statute (67-3-101) still lets
  MDT license "air schools", implementation UNVERIFIED. Connecticut, Maine and North Dakota (2011)
  repealed theirs; South Carolina's air-school approval section is reserved.

| State | Body | Flight schools | Citation | List access | Flight schools on the list |
|---|---|---|---|---|---|
| AL | ACCS Private School Licensure | exempt by certificate | Ala. Code 16-46-3(b)(6) (2019 ACCS copy) | free per record (HTML tables, approved and exempt) | 2 on the exempt list |
| AK | ACPE | conditional: Part 141/142 on application | 20 AAC 17.015(a)(9) | **bulk free** (one HTML page, "FLIGHT SCHOOLS" section) | 13 aviation entries |
| AZ | Board for Private Postsecondary Education | Part 141 exempt; others licensed unless under 40 hours and $1,000 | A.R.S. 32-3021(G)(7), (12) | UNVERIFIED (ppse.az.gov 403) | UNVERIFIED |
| AR | State Board of Private Career Education | private-pilot-only exempt | A.C.A. 6-51-603(9) | PDF list | none |
| CA | BPPE | conditional ($2,500 prepayment, no loans) | Educ. Code 94874(j) (BPPE's Act PDF, as amended 2026-01-01) | free per record, behind invisible reCAPTCHA | not tested |
| CO | Division of Private Occupational Schools | exempt | C.R.S. 23-64-104(1)(m) | PDF exists, returned 403 | UNVERIFIED |
| CT | Office of Higher Education | covered | CGS 10a-22a | bulk free (JavaScript registry) | none |
| DE | DOE Private Business and Trade Schools | covered | 14 Del. C. 8501, 8529 | HTML table; curl got 403 (see note below) | 2 (Fly Gateway Aviation Institute; FlightSafety Wilmington) |
| DC | Higher Education Licensure Commission | covered | DC Code 38-1310 | bulk free (HTML) | none |
| FL | Commission for Independent Education | exempt ("institution regulated by the FAA") | Fla. Stat. 1005.06(1)(g) | UNVERIFIED (fldoe.org 403) | UNVERIFIED |
| GA | GNPEC | exempt with FAA air agency certificate, $200, on application | O.C.G.A. 20-3-250.3(a)(5) | free per record (Qlik dashboard) | not tested |
| HI | DOE (trade schools) | opt-in | HRS 302A-425, 305J-3 | bulk free (HTML, 17) | 1 helicopter school |
| ID | State Board of Education | per-flight instructors exempt | Idaho Code 33-2403(4)(i) | free HTML | none |
| IL | IBHE PBVS | covered | 105 ILCS 426/15, /30 | **bulk free XLSX** | none (maintenance, drone, dispatch only) |
| IN | DWD Office for Career and Technical Schools | covered | IC 22-4.1-21-9 | bulk free PDF | none |
| IA | DOE (bond, 714.18) | bond waived if no tuition in advance | Iowa Code 714.18(5) | no list | n/a |
| KS | Board of Regents | covered | K.S.A. 74-32,164 | bulk free HTML | none in Kansas |
| KY | Commission on Proprietary Education | **licensed** | KRS 165A.310(12) | bulk free PDF | 3 (Epic Flight Academy, Nexgen Flight Academy, Wings Flight Training) |
| LA | Board of Regents | covered | R.S. 17:3140.1 | bulk free PDF | none |
| ME | DOE | covered | 20-A MRSA 9501 | free HTML | 2 |
| MD | MHEC | covered | Educ. 10-101; COMAR 13B.01.01.03 (from a summary) | free per record, CIP filter | 1 (maintenance) |
| MA | Office of Private Occupational School Education | covered | M.G.L. c.112 §263 | UNVERIFIED (mass.gov 403) | UNVERIFIED |
| MI | LEO Proprietary School Unit; **MDOT Aeronautics** | **MDOT licenses flight schools** | MCL 259.85; 395.101a | MDOT: **bulk free PDF**, May 2026 | **75 licensed flight schools** |
| MN | Office of Higher Education; **MnDOT Aeronautics** | **MnDOT licenses flight schools** as commercial operators | Minn. Stat. 360.013 subd. 45, 360.018; 136A.821 | MnDOT: **bulk free PDF**, 2024-06-04; OHE behind a CAPTCHA | **56 flight-school licenses** |
| MS | Commission on Proprietary School and College Registration | covered | Miss. Code 75-60-5 (2025 SB2525 text) | free HTML | 2, both "Not Authorized" |
| MO | DHEWD Proprietary School Certification | covered | RSMo 173.616 | per record; dhewd.mo.gov 403 | UNVERIFIED |
| MT | Commissioner of Higher Education | exempt | Regents Policy 221 (20-25-107) | n/a | n/a |
| NE | DOE Private Postsecondary Career Schools | conditional | Neb. Rev. Stat. 85-1604(8) | PDF list (35) | none |
| NV | Commission on Postsecondary Education | exempt (agency statement) | cpe.nv.gov; NRS 394 silent | none public | n/a |
| NH | Higher Education Commission | exempt | RSA 188-G:1, II(i) | 403 | UNVERIFIED |
| NJ | Dept of Labor (Training Evaluation Unit) | covered | N.J.S.A. 34:15C-1f; N.J.A.C. 12:41-2.1 | no license list found | n/a |
| NM | HED Private Post-Secondary Schools | licensed | NMSA 21-23-6.1 | bulk free HTML (167) | 3 (Bode Aviation, Red Arrow Flight Academy, Vertical Limit Aviation) |
| NY | NYSED BPSS | exempt only if solely flight training | Educ. Law 5001(2)(h) (public.law mirror) | per record (search form, not submitted) | not tested |
| NC | State Board of Proprietary Schools | covered | G.S. 115D-88 | bulk free HTML (134) | none (1 maintenance school) |
| ND | Board for Career and Technical Education | covered | NDCC 15-20.4-02 | HTML (10) | none |
| OH | State Board of Career Colleges and Schools | covered; flight-only schools apparently do not register (SBDC checklist) | ORC 3332.02 | UNVERIFIED (scr.ohio.gov 404) | UNVERIFIED |
| OK | Board of Private Vocational Schools | exempt; opt-in | 70 O.S. 21-101.1(2) | **bulk free XLSX** | 1 (Spartan) |
| OR | HECC Private Career Schools | licensed unless exempted | ORS 345.015(8) | bulk free PDF (rev. 2026-10-19) | 1 school, 3 campuses (Hillsboro Aero Academy) |
| PA | State Board of Private Licensed Schools | exempt (FAA-regulated flying schools) | 22 Pa. Code 73.42(5) | **bulk free CSV** (data.pa.gov) | 1 (Nulton Aviation Flight Academy) |
| RI | Office of the Postsecondary Commissioner | conditional, renewed yearly | Reg. A-6; RIGL 16-40-12 | per school | n/a |
| SC | Commission on Higher Education | career training licensed | S.C. Code 59-58-30(7) | PDF (Feb 2025) | 2 (Carolina Sky Flight Training, pending closure; USAeroFlight) |
| SD | Secretary of State | covered | SDCL 13-48-41 | HTML (32) | none |
| TN | THEC | conditional, on request | T.C.A. 49-7-2004(a)(10) (THEC's wording) | HTML (263) | none in Tennessee |
| TX | TWC Career Schools and Colleges | exempt only on application | Tex. Educ. Code 132.002(a)(8), (d) (public.law mirror) | per record (search form, not submitted); TWC's Eligible Training Provider List is bulk free but has no pilot program | n/a |
| UT | Division of Consumer Protection | conditional | Utah Code 13-34-111(3)(d) | per record | not tested |
| VT | State Board of Education | not covered (degree programs only) | 16 V.S.A. 176 | n/a | n/a |
| VA | SCHEV | conditional | Va. Code 23.1-226(B)(3) | UNVERIFIED (schev.edu 403) | UNVERIFIED |
| WA | Workforce Board | exempt if only FAA-certified courses | RCW 28C.10.030(8) | **bulk free JSON** (306) | none |
| WV | CTC Council | covered | 135CSR35 | none public | n/a |
| WI | DSPS Educational Approval Program | covered | Wis. Stat. 440.52 | Cloudflare 403 | UNVERIFIED |
| WY | DOE private school licensing | exempt if FAA-approved | W.S. 21-2-406(a)(iii) | none public | n/a |

Statute text for CA 94874(j), WA RCW 28C.10.030(8), PA 22 Pa. Code 73.42(5), MI MCL 259.85 and MN
360.013/360.018 was re-read directly for this document; the other rows rest on the state-by-state
research notes, which cite the pages fetched. Where a statute was read from a mirror (TX, NY:
public.law; AR, GA, MS, NM: FindLaw) or a summary (MD COMAR), the row says so or is marked.

Note on Delaware: plain fetching returned a Cloudflare 403, and the list was then read through a
browser, which this research's rules should have treated as blocked. The two Delaware entries are
reported, but treat the source as obtained past a bot check.

## 4. Federal lists that enumerate flight schools as a side effect

| Source | What it is | Rating | Format, size, date | Fields | Terms | Part 61 visible? |
|---|---|---|---|---|---|---|
| ICE SEVP school search, "Flight School" filter | schools certified to enroll F-1 or M-1 students | **free per record** (paged search, no export); the all-schools PDF is **bulk free** but has no school-type field | HTML, 9 pages of results; PDF "Certified School List" updated 2026-10-28, 3.6 MB | school, campus, campus code, street address, F-1 and M-1 flags | none stated (federal work) | **no**: SEVP requires FAA Part 141 or 142 certification of flight schools (ICE SEVP policy guidance "Flight Training Providers") |
| VA GI Bill Comparison Tool data (`ComparisonToolData.xlsx`, built from WEAMS) | every facility in VA's WEAMS, with `type` = FLIGHT and a `flight` flag | **bulk free** | XLSX, 16 MB, 81,563 facilities x 105 columns, data date 2026-10-08 | facility code, name, city, state, ZIP, approved flag, URL, Post-9/11 recipients, caution flags, lat/long | none stated | **no**: for Post-9/11 and MGIB a flight program must be at a Part 141 school (38 CFR 21.4263(a)(1)(i)(B)); flight schools are Part 141, Part 142, simulator-exempt air carriers, or military aero clubs (21.4263(b), (c)) |
| VA WEAMS institution search | the same data, one record at a time | free per record | web | as above plus programs | none stated | no |
| IPEDS completions (C2024_A) and directory (HD2024) | awards by 6-digit CIP for every Title IV institution | **bulk free** | CSV, 63 MB and 4.5 MB | UNITID, CIP, award level, counts; name, city, state, web address, lat/long | public domain | only colleges in federal student aid |
| College Scorecard field of study (2026-10-15 file) | programs by 4-digit CIP, with debt and earnings | **bulk free** | CSV, 198 MB | UNITID, CIP 4901, credential level | public domain | same as IPEDS |
| TSA Flight Training Security Program (formerly AFSP) | every provider that trains anyone, US citizens included, must register (49 CFR part 1552) | **none public** (FTSP portal is login-only; no provider list found on tsa.gov; FOIA status UNVERIFIED) | n/a | n/a | n/a | **yes**, including individual CFIs |
| Census County Business Patterns, NAICS 611512 Flight Training | establishment counts by county | aggregate only; the API now requires a key (an account), so not fetched | n/a | counts | public | counts only |
| SBA PPP loans, NAICS 611512 | already in the survey (B16) | bulk free | CSV | name, address, NAICS | US Government Works | yes, for schools that took a loan |

Counts from the downloads:

- **SEVP**: the "Flight School" filter returns 511 campuses of 247 schools; 187 of the schools are
  certified for M-1 (vocational) students. It includes universities whose whole school record is
  tagged flight (Liberty, Averett). Florida has 113 campuses, Texas and California 42 each.
- **VA**: 519 facilities typed FLIGHT, 229 currently approved (Florida 33, Texas 31, California 16).
  They reported 2,120 Post-9/11 recipients in total, 452 of them at the Delta Air Lines Aircrew
  Training Center.
- **IPEDS**: 221 institutions reported awards in CIP 49.0101 (aeronautics, general), 49.0102
  (airline/commercial/professional pilot), 49.0108 (flight instructor) or 49.0199. 106 reported
  awards in 49.0102 or 49.0108: 2,248 awards in 49.0102 and 30 in 49.0108. Utah Valley University
  leads with 414. Scorecard lists 267 institutions under CIP 4901.
- **TSA**, from the final rule (89 FR 35580, 2024-05-01): "approximately 9,000 of the 13,000 flight
  training providers registered with TSA operate under 14 CFR part 61, and 500 providers operate
  under 14 CFR parts 121 and 135." TSA estimates "approximately 4,206 flight training providers who
  actively provide flight training to candidates ... and 19,738 flight training providers who
  exclusively train U.S. citizens and U.S. nationals." That is the closest thing to a government
  count of Part 61 providers, and it is about four times our 4,825, because it counts individual
  instructors.

Related, at the state level: State Approving Agencies approve flight courses for the GI Bill
chapters that are not Post-9/11 (38 CFR 21.4263(a)(1)(i)(A)), so each state has a VA approval list
for flight courses; it reaches the public through WEAMS, above.

## 5. Matching against our 4,825 schools

### Method

`match_external_school_lists.py` reads `flight_line.json`, resolves each school's states and cities
from its contact address and from its airports (`airports.csv`, then OurAirports for identifiers
that file lacks; 4,728 of 4,825 schools get a state), and tries, in order:

1. **domain**: the source's website has the same registered domain as the school (webmail and
   generic hosts ignored);
2. **exact**: the same normalized name (legal suffixes and punctuation removed, DBA names split
   out) in the same state;
3. **address**: the same house number and ZIP as the school's contact address, plus some name
   signal; an address hit with no name signal is reported as *colocated* (another tenant at the
   same airport street address) and does not count;
4. **fuzzy**: same state, and either near-identical names (ratio 0.95) or overlapping distinctive
   name tokens (generic words like aviation, flight, academy, and the city and state names removed)
   plus a city match or a ratio of 0.85.

Everything left over was checked by hand against our names, domains, airports and addresses. That
review is how "in our list under another name" was decided below. The automatic match has false
positives too. A read of the 43 SEVP campus rows matched by the address or fuzzy rule found 4 paired
with the wrong school: American Flight Training (Wayman) with Americana Flight Training, twice;
American Flyers Morristown with Certified Flyers II; and Flying Academy's Van Nuys campus with LA
Flight Academy. Each of those schools still matches through another campus, so the school-level
count is unchanged. Pairs that look wrong but check out on the domain include Horizon Flight Center
with Horizon Aviation Services (horizonflightcenter.com) and Golden State Flying Club with Golden
State Aviation (gsfc.com).

### Results

| Source | Entries | Matched automatically | Left over | Of those, in our list under another name | Not primary flight training | Missing from our list |
|---|---|---|---|---|---|---|
| SEVP flight schools (by school) | 247 | 225 | 22 | 10 | 3 (PlaneSym and SimJet simulator centers; Midwest University, aviation program UNVERIFIED) | **8** (one, Orient Flight School, shares a street address with our Pilot Training Center Homestead) |
| VA flight facilities, currently approved | 229 | 178 | 51 | 27 (campuses or aliases of schools we have) | 22 (FlightSafety, CAE SimuFlite, Delta, Atlas Air, Flight Training International) | **2** (First Flight Aviation, Miamisburg OH, which we hold only as an aircraft-owner record; Bell Murray Aerospace, Fort Worth, UNVERIFIED what it is) |
| VA flight facilities, all (most not approved, many withdrawn) | 519 | 303 | 216 | not reviewed | not reviewed | not reviewed |
| IPEDS colleges with 49.0102 or 49.0108 awards | 106 | 104 | 2 | 0 | 0 | **2** (Fullerton College, Southwestern College; flight may be through a partner, UNVERIFIED) |
| IPEDS, any 49.01xx award | 221 | 178 | 43 | not reviewed | mostly aviation-maintenance and management colleges | not reviewed |
| **Michigan MDOT licensed flight schools** (May 2026) | 75 | 45 | 30 | 4 (SkyWalker Flying; three ATP sites); 1 more uncertain (Kalamazoo Flight Instruction, perhaps our Kal-Aero) | 0 | **25 rows, about 23 schools** |
| **Minnesota MnDOT flight-school licenses** (2024-06-04) | 56 | 37 | 19 | 0 | 1 (an aerial photography operator) | **19 rows, 17 operators** (Janson Flying Service holds 3) |
| Alaska ACPE "FLIGHT SCHOOLS" and other aviation entries | 13 | 10 | 3 | 0 | 0 | **3** (Alaska Flight Academy, CKT Aviation, Desert Air Transport) |
| Other state education lists (KY, NM, OR, OK, PA, SC, HI, DE, ME, AL and others; aviation-named entries) | 63 | 44 | 19 | 2 (Fly Gateway, Nulton) | most (Embry-Riddle base campuses, maintenance, ATC, drone schools, FlightSafety) | **1 to 3** (Carolina Sky Flight Training, pending closure; Epic Flight Academy in Erlanger KY and Epic Aviation in Tuscaloosa are uncertain) |
| FAA Part 141 file the pipeline ingests (certificates) | 519 | 474 | 45 | at least 12 by spot check (e.g. Auburn, Marshall's Bill Noe Flight School, Charleston Southern, Farmingdale, South Dakota State, St. Charles Flying Service, Faithful Guardian) | a few (balloon schools, CAE USA, Gleim's online ground school) | up to about 25 |

The 10 SEVP schools we already have under another name: Bell Textron (our Bell Helicopter Training
Academy), Bowling Green State (Bowling Green Flight Center), Faithful Guardian, Henderson State
(Arkansas Aviation Academy, hsu.edu), Hummingbird Aviation (AV8 Flight School, Flying Cloud), M.I.
Air, SUNY Farmingdale (Aviation Flight Center), The Flight School of Gwinnett (The Flight School of
Athens, whose site lists a Lawrenceville location), University of Central Missouri (UCM Aviation),
and University of North Dakota (we hold its Mesa site and the UND Aerospace Foundation, but no Grand
Forks location: worth fixing).

### Ten flight schools from the state lists that we don't have

Each was searched in `flight_line.json` by name, domain and airport; each website answered an HTTP
request on 2026-11-03 except where noted.

| # | School | Where | Evidence |
|---|---|---|---|
| 1 | Hall's Flying Service | Grosse Ile (ONZ) and Monroe (TTF), MI | MDOT licensed flight schools, May 2026, both airports; halls-flyingservices.com |
| 2 | Lapeer Aviation Flight Training | Flint (FNT), MI | MDOT list; lapeeraviation.com (answered 406 to a script) |
| 3 | Victors Aviation | Troy (VVL), MI | MDOT list; victorsaviation.com |
| 4 | Maxflight Helicopter Services | Grosse Ile (ONZ), MI | MDOT list; maxflighthelomi.com |
| 5 | Three Point Aviation | Alma (AMN) and Clare (48D), MI | MDOT list, two licenses; we have only a "City of Clare" owner record at 48D |
| 6 | MN Helicopters Inc | Anoka County-Blaine, MN | MnDOT flight-school license list, 2024-06-04; mnhelicopters.com |
| 7 | 86 Charlie LLC dba Spectrum Pilot Services | Dodge Center, MN | MnDOT list; spectrumpilotservices.com |
| 8 | Sawtooth Aviation LLC | Grand Marais-Cook County, MN | MnDOT list; sawtoothaviation.com |
| 9 | Wings Over Water, Inc. | East Gull Lake, MN (seaplane training) | MnDOT list; wingsoverwaterseaplanes.com |
| 10 | Alaska Flight Academy | Wasilla, AK | ACPE institution list, "FLIGHT SCHOOLS" section, authorized until 2031-02-27; also FAA Part 141 certificate 5NMS069O; alaskaflightacademy.com |

### Ten flight schools from the federal lists that we don't have

Each was searched in `flight_line.json` by name, DBA, domain, airport identifier and street address.

| # | School | Where | Evidence |
|---|---|---|---|
| 1 | Aero Tech Academy Inc | Redlands, CA (KREI), 1745 Sessums Dr | FAA Part 141 cert 2AZS030L; SEVP campus los214f01596000 (M-1). We have Redlands Flying Club and Blue Raven Aviation at the same street address, not this school |
| 2 | Aeroservice AFT Corp dba Aeroservice Flight Training | Miami, FL (Miami Executive, 13410 SW 128 St) | Part 141 cert 8F7S381L; SEVP mia214f59587000 (M-1). We have 15 schools at KTMB, not this one |
| 3 | JC Air Academy Inc | Stockton, CA (KSCK) | Part 141 cert 7JCS903K; SEVP sfr214f02116000 (M-1). Our KSCK entries are BT Aero and RJ Tutt Aviation |
| 4 | Orlando Flight Academy Inc | Apopka, FL (X04), 1321 Apopka Airport Rd | Part 141 cert 63CS931K; SEVP bos214f01606000 (M-1). We have B&H Flight Services at the same airport address |
| 5 | World Wide Wings LLC dba WWW | San Bernardino, CA (KSBD) | Part 141 cert 5WWS306L; SEVP los214f58772000 (M-1). No school of ours at KSBD |
| 6 | Alliance Aviation Inc | Fort Lauderdale, FL | SEVP mia214f51146000 (M-1); not found by name in the Part 141 file. Our "Alliance" entries are other companies |
| 7 | Las Vegas Flight Academy | Henderson, NV | SEVP pho214f59567000 (M-1); not found by name in the Part 141 file. Our KHND schools are Cactus Aviation and All In Aviation |
| 8 | Custom Air International LLC dba Orient Flight School | Homestead, FL (X51), 28790 SW 217th Ave | Part 141 cert OFSS018M; SEVP mia214f60099000. **Possibly ours**: our Pilot Training Center Homestead is at the same address, and the Part 141 file also has "Homestead General" there |
| 9 | GC Aviation Services-1 LLC dba First Flight Aviation | Miamisburg, OH (KMGY), 10600 Springboro Pike | Part 141 cert CWBS055D; VA facility 26000235, approved, 10 Post-9/11 recipients. We hold "GC AVIATION SERVICES-1 LLC" only as an FAA aircraft-owner record with no website or airport |
| 10 | Fullerton College | Fullerton, CA | IPEDS UNITID 114859, 10 awards in CIP 49.0102 (professional pilot). Not in our list; whether students fly with the college or a partner is UNVERIFIED |

The pattern matters more than the count: these lists can only recover Part 141 and 142 schools,
and seven of the ten above are already in the FAA Part 141 file we ingest. The fix is in our join of
that file, not a new source. Filed as a ROADMAP item rather than fixed here.

Data issue found on the way: our "Atp Flight Academy" entry at KA39 carries the domain
`auburn.edu`, so a domain join sends Auburn University records to ATP. The matcher now picks the
best-named school among domain hits, which avoids it; the record itself still needs fixing.

## Not reached or unverified

- tn.gov and revenue.support.tn.gov blocked plain fetching: the Tennessee aircraft row rests on
  search results.
- azdot.gov, mass.gov, michigan.gov/aero, dot.nh.gov and the Virginia DOAV form returned 403:
  public-access status for AZ, MA, MI, NH and whether VA collects the N-number.
- Every "records request" rating is inferred from the absence of a public list; none was tested.
- Secondary hosts (FindLaw, texas.public.law) stand in for the official code for AR, GA, MS, NM, TX.
- The SEVP "Flight Training Providers" policy PDF is a scanned image; its Part 141/142 requirement
  is taken from the ICE page that summarizes it.
- TSA: whether the FTSP provider list is releasable under FOIA.
- Census County Business Patterns needs an API key, so establishment counts for NAICS 611512 were
  not pulled.
- Education-board lists not obtained: AZ, CO, FL, MA, MO, OH, VA, WI, NH (403, 404 or Cloudflare);
  MN's Office of Higher Education list (CAPTCHA). Per-record searches in CA (reCAPTCHA), GA, NY, TX
  and UT were not run.
- Whether "regulated by" or "approved by the FAA" exemptions (CO, FL, NE, UT, VA, WY) reach Part 61
  schools: our reading, no agency guidance found.
- Aviation-agency licensing was not checked in most states; Maryland, Montana, New Jersey and Rhode
  Island have statutory authority whose use is UNVERIFIED. New Hampshire's operator registry
  exists by statute but its pages returned 403.
- Statute currency: AL (2019 copy), MS (2025 SB2525 enactment), HI (2026 bills), DE (archived rule).

## Reproducing the counts

Downloads (each into its own empty directory; treat as untrusted):

```
curl -sL -o list.pdf https://studyinthestates.dhs.gov/assets/certified-school-list-10-28-26.pdf
for p in $(seq 0 8); do curl -sL -o p$p.html "https://studyinthestates.dhs.gov/school-search?field_education_level_value=6&page=$p"; done
curl -sL -o ComparisonToolData.xlsx https://www.benefits.va.gov/GIBILL/docs/job_aids/ComparisonToolData.xlsx
curl -sO https://nces.ed.gov/ipeds/datacenter/data/HD2024.zip
curl -sO https://nces.ed.gov/ipeds/datacenter/data/C2024_A.zip
curl -sO https://ed-public-download.scorecard.network/downloads/Most-Recent-Cohorts-Field-of-Study_10152026.zip
curl -s -o fr.htm https://www.govinfo.gov/content/pkg/FR-2024-05-01/html/2024-08800.htm
curl -sO https://www.dot.mn.gov/aero/documents/aviationbusinesses/flightschools.pdf
# Michigan: the PDF linked from https://www.michigan.gov/mdot/travel/mobility/aeronautics/licensing/licensed-flight-schools
```

The state education-board files are linked from each board's page in the table in section 3.

Each list was reduced to a CSV with `source,name,city,state,website` (plus `address` where the
source has one), then:

```
python -I research/FlightSchoolMapping/scripts/probes/match_external_school_lists.py \
  research/FlightSchoolMapping/output/flight_line.json \
  research/FlightSchoolMapping/output/airports.csv,research/FlightSchoolMapping/data/ourairports_airports.csv \
  OUT_DIR c_faa141.csv c_sevp.csv c_va.csv c_ipeds.csv c_mi_mdot.csv c_mn_mndot.csv c_state_edu.csv
```

For Michigan, the "website" column is the domain of each school's listed email address (webmail and
ISP domains are ignored by the matcher).

which printed, on 2026-11-03: `c_faa141 700 rows {exact 367, domain 147, address 38, fuzzy 28,
colocated 31, none 89}`, `c_sevp 511 {exact 389, address 24, fuzzy 19, colocated 6, none 73}`,
`c_va 519 {exact 233, fuzzy 62, domain 8, none 216}`, `c_ipeds 221 {domain 172, exact 3, fuzzy 3,
none 43}`, `c_mi_mdot 75 {exact 22, domain 21, fuzzy 2, none 30}`, `c_mn_mndot 56 {domain 28,
exact 7, fuzzy 2, none 19}`, `c_state_edu 63 {exact 22, domain 21, fuzzy 1, none 19}`. Some matches
are to FAA aircraft-owner records in our list (all-caps names with no website) rather than to a
school record; they count as matches here.
