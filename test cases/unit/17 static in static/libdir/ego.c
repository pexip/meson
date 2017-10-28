#include "ego.h"
#include <stdio.h>
#include "iam.h"
#include "thebest.h"

void
ego_msg ()
{
  printf ("%s %s", i_am(), the_best());
}
