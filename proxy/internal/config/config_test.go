/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

package config

import "testing"

func TestModelAliasesAreRead(t *testing.T) {
	aliases, err := parseModelAliases(`{"thinkube-fast":"nvidia/Qwen3.6-35B-A3B-NVFP4"}`)
	if err != nil || aliases["thinkube-fast"] != "nvidia/Qwen3.6-35B-A3B-NVFP4" {
		t.Fatalf("aliases %v, error %v", aliases, err)
	}
}

func TestEmptyAliasTableIsAllowed(t *testing.T) {
	aliases, err := parseModelAliases(`{}`)
	if err != nil || len(aliases) != 0 {
		t.Fatalf("aliases %v, error %v", aliases, err)
	}
}

func TestMissingOrMalformedAliasTableIsAnError(t *testing.T) {
	for _, raw := range []string{"", "null-ish", `["thinkube-fast"]`} {
		if _, err := parseModelAliases(raw); err == nil {
			t.Errorf("%q: no error", raw)
		}
	}
}
