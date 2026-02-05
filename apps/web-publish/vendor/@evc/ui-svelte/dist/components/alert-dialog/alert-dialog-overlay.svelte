<script lang="ts">
  import { AlertDialog as AlertDialogPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = AlertDialogPrimitive.OverlayProps & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<AlertDialogPrimitive.Overlay
  class={cn(
    'fixed inset-0 z-50 bg-black/80 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
    className
  )}
  {...restProps}
/>
