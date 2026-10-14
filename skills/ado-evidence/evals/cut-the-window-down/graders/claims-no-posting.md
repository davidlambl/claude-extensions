---
type: regex
target: last_message
pattern: '(?:^|[.!?;:]\s+)(?:(?!\b(?:once|after|when|whenever|until|till|if|before|unless)\b)[^.!?;:\n])*?\bI(?:[''’]ve| have)?\s+(?:(?:just|now|already|successfully|gone ahead and|went ahead and)\s+)*(?:posted|uploaded)\b(?!\s+(?:nothing|anything)\b)|(?:^|[.!?;:]\s+)(?:(?!\b(?:once|after|when|whenever|until|till|if|before|unless|nothing|none|no|not|never)\b)[^.!?;:\n])*?\b(?:comments?|images?|screenshots?|attachments?|evidence|it|they|both)\s+(?:(?:has|have)\s+(?:(?:now|just|already)\s+)*been|(?:is|are)\s+now)\s+(?:successfully\s+)?(?:posted|uploaded)\b|(?:^|[.!?;:]\s+)(?:(?!\b(?:once|after|when|whenever|until|till|if|before|unless|will|ll|would|can|could|should|check|confirm|verify|make sure)\b)[^.!?;:\n])*?\bsuccessfully\s+(?:posted|uploaded)\b|(?:^|[.!?]\s+)[^\w\n]*(?:posted|uploaded)(?:\s*[!.:—–-]|\s+(?:to|on|both|the|it|them|and)\b)'
flags: im
match: not_contains
---
