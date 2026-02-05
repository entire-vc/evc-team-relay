<script lang="ts">
  import { AlertDialog as AlertDialogPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';
  import { buttonVariants } from '../button/variants.js';

  type Props = AlertDialogPrimitive.CancelProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<AlertDialogPrimitive.Cancel
  class={cn(buttonVariants({ variant: 'outline' }), 'mt-2 sm:mt-0', className)}
  {...restProps}
>
  {@render children?.()}
</AlertDialogPrimitive.Cancel>
