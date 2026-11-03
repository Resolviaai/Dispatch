/**
 * test_back_handler.ts
 * Verifies Dispatch Unified Back-Stack Navigation System.
 */

import { registerBackHandler, executeBack } from './src/navigation/backHandler';

console.log('🧪 Starting Dispatch Back-Stack Navigation Test Suite...\n');

// Test 1: Priority sorting (higher priority executes first)
let executionOrder: string[] = [];

const unregister10 = registerBackHandler('tab_history', 10, () => {
  executionOrder.push('tab_history');
  return true;
});

const unregister40 = registerBackHandler('modal', 40, () => {
  executionOrder.push('modal');
  return true;
});

const unregister20 = registerBackHandler('subscreen', 20, () => {
  executionOrder.push('subscreen');
  return true;
});

console.log('Test 1: Priority sorting...');
const handled1 = executeBack();
if (!handled1 || executionOrder[0] !== 'modal') {
  throw new Error(`Failed: expected modal to execute first, got ${executionOrder[0]}`);
}
console.log('  ✓ Priority 40 (modal) executed before lower priorities');

// Test 2: Propagation stops when handled (returns true)
if (executionOrder.length !== 1) {
  throw new Error(`Failed: expected exactly 1 handler to run when true is returned, got ${executionOrder.length}`);
}
console.log('  ✓ Propagation halted immediately when handler consumed event');

// Test 3: Fallthrough to next priority when handler returns false
executionOrder = [];
unregister40(); // Unregister modal

const unregisterPassThrough = registerBackHandler('modal_noop', 40, () => {
  executionOrder.push('modal_noop');
  return false; // Not handled, pass through!
});

console.log('Test 3: Fallthrough when handler returns false...');
const handled2 = executeBack();
if (!handled2 || executionOrder[0] !== 'modal_noop' || executionOrder[1] !== 'subscreen') {
  throw new Error(`Failed: expected fallthrough from modal_noop to subscreen, got ${executionOrder.join(' -> ')}`);
}
console.log('  ✓ Fallthrough correctly cascades down priority stack when unhandled');

// Test 4: Returns false when all handlers return false or empty
unregisterPassThrough();
unregister20();
unregister10();

console.log('Test 4: Root screen returns false...');
const handledRoot = executeBack();
if (handledRoot) {
  throw new Error('Failed: expected executeBack to return false when stack is empty/at root');
}
console.log('  ✓ Returns false when stack is empty, allowing native exit guard to engage');

console.log('\n🎉 ALL DISPATCH BACK-STACK NAVIGATION TESTS PASSED 100%!\n');
