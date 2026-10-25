/* SPDX-License-Identifier: GPL-2.0-only */
/* GFW status regression checks; no GPU or private capture required. */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include "../vendor/gfw_eval.h"

static unsigned checks;

static void expect(const char *name, const struct gpu_unlock_gfw_sample *sample,
		   int expected)
{
	int actual = gpu_unlock_gfw_assess(sample);

	if (actual != expected) {
		fprintf(stderr, "%s: expected %d, got %d\n", name, expected, actual);
		exit(1);
	}
	checks++;
}

int main(void)
{
	const struct gpu_unlock_gfw_sample ready = {
		.config_ok = true,
		.plm = 0x8b8f,
		.boot = 0x3ff,
		.cpuctl = GU_GFW_CPUCTL_HALTED,
	};
	struct gpu_unlock_gfw_sample sample = ready;

	expect("missing sample", NULL, -ENODEV);
	sample.config_ok = false;
	expect("inaccessible PCI config", &sample, -ENODEV);
	expect("completed GFW", &ready, 0);

	for (unsigned field = 0; field < 3; field++) {
		sample = ready;
		if (field == 0)
			sample.plm = 0;
		else if (field == 1)
			sample.boot = 0;
		else
			sample.cpuctl = 0;
		expect("GFW still running", &sample, -EAGAIN);

		sample = ready;
		if (field == 0)
			sample.plm = (u32)~0U;
		else if (field == 1)
			sample.boot = (u32)~0U;
		else
			sample.cpuctl = (u32)~0U;
		expect("all-ones read", &sample, -ENODEV);
	}

	/* Each PRI value deliberately passes its field's completion-bit test. */
	for (unsigned mask = 1; mask < 8; mask++) {
		sample = ready;
		if (mask & 1)
			sample.plm = 0xbadf0001;
		if (mask & 2)
			sample.boot = 0xbadf00ff;
		if (mask & 4)
			sample.cpuctl = 0xbadf0010;
		expect("PRI error must keep polling", &sample, -EAGAIN);
	}

	sample = ready;
	sample.plm = 0xbadf0001;
	sample.boot = (u32)~0U;
	expect("all-ones still means inaccessible", &sample, -ENODEV);
	sample.boot = 0xbadf00ff;
	sample.config_ok = false;
	expect("PCI failure precedes PRI retry", &sample, -ENODEV);

	printf("%u GFW status checks passed; all register values simulated.\n", checks);
	return 0;
}
