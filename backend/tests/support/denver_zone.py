"""America/Denver by the US rules since 2007, as a fixed-rule tzinfo (a test helper, not a test).

The machine has no tzdata package, and a DST test must not depend on the machine's own zone. MST = UTC-7; daylight time (MDT, UTC-6) runs from the second Sunday of
March 02:00 to the first Sunday of November 02:00 (local wall clock). The hour skipped in spring (02:00-03:00) and the hour repeated in autumn (01:00-02:00) follow
PEP 495: `fold` picks the offset in force before the transition for a skipped time and the first pass for a repeated one.
"""
import datetime as dt

ZERO, HOUR = dt.timedelta(0), dt.timedelta(hours=1)


def first_sunday_on_or_after(moment):
    days_to_go = 6 - moment.weekday()
    return moment + dt.timedelta(days_to_go) if days_to_go else moment


class Denver(dt.tzinfo):
    std = dt.timedelta(hours=-7)

    @staticmethod
    def _edges(year):
        return first_sunday_on_or_after(dt.datetime(year, 3, 8, 2)), first_sunday_on_or_after(dt.datetime(year, 11, 1, 2))   # local wall clock

    def utcoffset(self, moment):
        return self.std + self.dst(moment)

    def tzname(self, moment):
        return 'MDT' if self.dst(moment) else 'MST'

    def dst(self, moment):
        if moment is None:
            return ZERO
        start, end = self._edges(moment.year)
        wall = moment.replace(tzinfo=None)
        if wall < start or wall >= end:
            return ZERO
        if wall < start + HOUR:
            return HOUR if moment.fold else ZERO
        if wall < end - HOUR:
            return HOUR
        return ZERO if moment.fold else HOUR

    def fromutc(self, moment):
        naive = moment.replace(tzinfo=None)
        start, end = self._edges(naive.year)
        spring, fall = start - self.std, end - self.std - HOUR                  # the two instants, in UTC
        if spring <= naive < fall:
            return (naive + self.std + HOUR).replace(tzinfo=self)
        local = (naive + self.std).replace(tzinfo=self)
        return local.replace(fold=1) if fall <= naive < fall + HOUR else local


def utc(year, month, day, hour=0, minute=0, second=0):
    """An epoch time from a UTC wall clock."""
    return dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.timezone.utc).timestamp()
